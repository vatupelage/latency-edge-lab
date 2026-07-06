"""Phase 1 data logger — captures the Polymarket `market` WS stream (full L2,
both sides, both tokens) for every live BTC Up/Down 5m and 15m window, turns it
into top-of-book + per-token OFI rows (via the unit-tested WindowRecorder), and
writes one Parquet file per window plus a metadata line.

Design choices (see ACCESS_NOTES.md for the verified protocol):
  - Credential-free: WS + Gamma are keyless; this process never loads the wallet.
  - Restart-safe by construction: one immutable Parquet file per closed window
    (named by slug). A mid-window restart loses that window's partial buffer
    (a gap, never a dup or a corrupt append). Closed files are never rewritten.
  - One asyncio task per horizon rolls windows back-to-back; horizons run
    concurrently (a 15m window overlaps several 5m windows).
  - The same per-window event path is reused by replay mode (see replay.py).

Run forward:   python3 -m edgelab.logger
"""

import asyncio
import json
import os
import time
from datetime import datetime, timezone

import requests

try:
    import websockets
    _HAVE_WS = True
except Exception:
    _HAVE_WS = False

import pyarrow as pa
import pyarrow.parquet as pq

from edgelab.recorder import WindowRecorder

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"


# ----------------------------- gamma helpers --------------------------------

def resolve_tokens(slug: str):
    """slug -> (up_token, down_token) or None (window not listed yet)."""
    try:
        r = requests.get(f"{GAMMA_EVENTS_URL}?slug={slug}", timeout=6)
        evs = r.json()
        if not evs:
            return None
        mkt = evs[0]["markets"][0]
        toks = mkt.get("clobTokenIds")
        toks = json.loads(toks) if isinstance(toks, str) else toks
        outs = mkt.get("outcomes", '["Up","Down"]')
        outs = json.loads(outs) if isinstance(outs, str) else outs
        up_idx = 0 if str(outs[0]).lower().startswith("up") else 1
        return toks[up_idx], toks[1 - up_idx]
    except Exception:
        return None


def fetch_outcome(slug: str):
    """(up_won: bool|None, up_price: float|None). up_won True iff Up price ~1."""
    try:
        r = requests.get(f"{GAMMA_EVENTS_URL}?slug={slug}", timeout=6)
        evs = r.json()
        if not evs:
            return None, None
        mkt = evs[0]["markets"][0]
        prices = mkt.get("outcomePrices")
        prices = json.loads(prices) if isinstance(prices, str) else prices
        outs = mkt.get("outcomes", '["Up","Down"]')
        outs = json.loads(outs) if isinstance(outs, str) else outs
        up_idx = 0 if str(outs[0]).lower().startswith("up") else 1
        up_price = float(prices[up_idx]) if prices else None
        if up_price is None:
            return None, None
        return (up_price >= 0.99), up_price
    except Exception:
        return None, None


# ----------------------------- parquet writer -------------------------------

def write_window(recorder: WindowRecorder, out_dir: str, meta: dict) -> str | None:
    """Write the window's rows to events/day=.../horizon=.../<slug>.parquet and
    append `meta` to windows.jsonl. Returns the parquet path (or None if empty)."""
    if not recorder.rows:
        _append_meta(out_dir, {**meta, "n_rows": 0, "parquet": None})
        return None
    day = datetime.fromtimestamp(recorder.open_ts, tz=timezone.utc).strftime("%Y-%m-%d")
    part = os.path.join(out_dir, "events", f"day={day}", f"horizon={recorder.horizon}")
    os.makedirs(part, exist_ok=True)
    path = os.path.join(part, f"{recorder.slug}.parquet")
    # `horizon` is encoded in the partition path (horizon=...); drop the
    # redundant in-file column so a partitioned read doesn't see a type clash.
    cols = [{k: v for k, v in r.items() if k != "horizon"} for r in recorder.rows]
    tbl = pa.Table.from_pylist(cols)
    pq.write_table(tbl, path, compression="zstd")
    _append_meta(out_dir, {**meta, "parquet": os.path.relpath(path, out_dir)})
    return path


def _append_meta(out_dir: str, meta: dict) -> None:
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "windows.jsonl"), "a") as f:
        f.write(json.dumps(meta) + "\n")


# ----------------------------- streaming ------------------------------------

async def stream_window(slug, horizon, up_token, down_token, open_ts, close_ts,
                        out_dir, log):
    """Connect, subscribe to both tokens, feed the recorder until close, then
    persist rows + (best-effort) outcome metadata."""
    rec = WindowRecorder(slug, horizon, up_token, down_token, open_ts, close_ts)
    stop_at = float(close_ts)
    try:
        async with websockets.connect(WS_URL, ping_interval=10, close_timeout=3,
                                      compression=None, max_size=2**21,
                                      max_queue=64) as ws:
            await ws.send(json.dumps({"type": "market",
                                      "assets_ids": [up_token, down_token]}))
            while time.time() < stop_at:
                timeout = max(0.25, stop_at - time.time())
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    break
                recv_ts = time.time()
                msgs = json.loads(raw)
                if isinstance(msgs, dict):
                    msgs = [msgs]
                for m in msgs:
                    rec.on_ws_message(m, recv_ts)
    except Exception as e:
        log(f"{slug} ws_error {type(e).__name__}: {e}")
    # persist rows immediately (never lose data to a slow resolution fetch)
    up_won, up_price = await _await_outcome(slug, close_ts, log)
    meta = rec.finalize(up_won=up_won, terminal=up_price, feed="polymarket_gamma")
    write_window(rec, out_dir, meta)
    log(f"{slug} done rows={len(rec.rows)} up_won={up_won}")


async def _await_outcome(slug, close_ts, log, poll=20, wait=240):
    """Best-effort: poll Gamma for the resolved outcome after close."""
    deadline = close_ts + wait
    while time.time() < deadline:
        up_won, up_price = fetch_outcome(slug)
        if up_won is not None:
            return up_won, up_price
        await asyncio.sleep(poll)
    return None, None


async def horizon_loop(horizon, period, out_dir, log):
    """Roll windows for one horizon back-to-back, forever."""
    seen = set()
    while True:
        now = int(time.time())
        start = (now // period) * period
        slug = f"btc-updown-{horizon}-{start}"
        if slug in seen:
            await asyncio.sleep(1.0)
            continue
        toks = resolve_tokens(slug)
        if toks is None:
            await asyncio.sleep(2.0)
            continue
        seen.add(slug)
        if len(seen) > 50:
            seen = set(list(seen)[-25:])
        up_token, down_token = toks
        close_ts = start + period
        log(f"{slug} streaming (closes in {int(close_ts - time.time())}s)")
        try:
            await stream_window(slug, horizon, up_token, down_token,
                                start, close_ts, out_dir, log)
        except Exception as e:
            log(f"{slug} window_error {type(e).__name__}: {e}")


def _stamped(msg: str) -> None:
    print(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {msg}", flush=True)


async def main():
    import yaml
    cfg_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)["logger"]
    # EDGELAB_OUT overrides the config out_dir (server keeps data on /mnt/data,
    # which has headroom; the root fs does not).
    out_dir = os.environ.get("EDGELAB_OUT", cfg["out_dir"])
    horizons = cfg["horizons"]
    _stamped(f"edgelab logger start: horizons={list(horizons)} out={out_dir}")
    if not _HAVE_WS:
        _stamped("FATAL: websockets not installed")
        return
    await asyncio.gather(*[
        horizon_loop(h, int(p), out_dir, _stamped) for h, p in horizons.items()
    ])


if __name__ == "__main__":
    asyncio.run(main())
