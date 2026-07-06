# predictor/edgelab/harness.py
"""Single-region capture harness entrypoint. Record-only: imports nothing from
live_trader; the only Polygon endpoint touched is a read-only RPC ping."""
import argparse
import asyncio
import json
import os

import yaml

from edgelab.clockstamp import ClockStamper
from edgelab.seqgap import SeqGapTracker
from edgelab.writer import RotatingParquetWriter
from edgelab import collect

_CFG = os.path.join(os.path.dirname(__file__), "config.yaml")
BINANCE_BASE = "wss://stream.binance.com:9443/stream?streams="
COINBASE_WS = "wss://ws-feed.exchange.coinbase.com"
ORACLE_WS = "wss://ws-live-data.polymarket.com"
ORACLE_SUB = {"action": "subscribe",
              "subscriptions": [{"topic": "crypto_prices", "type": "update"}]}

def _make_ctx(region_id, symbols):
    return collect.BuildCtx(
        stamper=ClockStamper(region_id), gaps=SeqGapTracker(),
        symbol=list(symbols)[0], symbols=set(symbols), token_index={})


def dry_run(sample_path, out_dir, region_id="test", symbols=("BTC",)) -> dict:
    ctx = collect.BuildCtx(
        stamper=ClockStamper(region_id, reader=lambda: (0, 1)),
        gaps=SeqGapTracker(), symbol=symbols[0], symbols=set(symbols),
        token_index={})
    writer = RotatingParquetWriter(out_dir, region_id=region_id)
    n = 0
    with open(sample_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if "token_index" in obj:
                ctx.token_index = {k: tuple(v) for k, v in obj["token_index"].items()}
                continue
            for row in collect.build_rows(obj["family"], obj["raw"], ctx):
                writer.write(row)
                n += 1
    writer.flush_all()
    return {"rows": n}


async def run_all(cfg, out_dir):
    region_id = cfg["region_id"]
    symbols = cfg["symbols"]
    horizons = tuple((k, int(v)) for k, v in cfg["horizons"].items())
    ctx = _make_ctx(region_id, symbols)
    writer = RotatingParquetWriter(out_dir, region_id=region_id)
    stop = asyncio.Event()
    log = lambda m: print(m, flush=True)
    sym = symbols[0].lower()
    streams = f"{sym}usdt@trade/{sym}usdt@bookTicker"
    cb_sub = {"type": "subscribe",
              "product_ids": [f"{symbols[0]}-USD"], "channels": ["matches", "ticker"]}
    tasks = [
        collect.run_feed("binance", BINANCE_BASE + streams, None, "binance", ctx, writer, stop, log),
        collect.run_feed("coinbase", COINBASE_WS, cb_sub, "coinbase", ctx, writer, stop, log),
        collect.run_feed("pm_oracle", ORACLE_WS, ORACLE_SUB, "pm_oracle", ctx, writer, stop, log),
        collect.run_pm_clob(ctx, writer, stop, log, horizons),
        collect.run_probes(cfg["rpc_url"], cfg["tls_hosts"], ctx, writer, stop,
                           float(cfg.get("probe_period_s", 2.0)),
                           clob_url=cfg.get("clob_url")),
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        writer.flush_all()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", dest="dry", default=None,
                    help="replay a sample JSONL through build_rows (no network)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    cfg = yaml.safe_load(open(_CFG))["harness"]
    out = os.environ.get("EDGELAB_OUT", args.out or cfg["out_dir"])
    if args.dry:
        print(dry_run(args.dry, out))
        return
    asyncio.run(run_all(cfg, out))


if __name__ == "__main__":
    main()
