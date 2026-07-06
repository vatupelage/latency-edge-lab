"""The seam between the network loop and the tested units. `build_rows` turns one
raw feed message into fully-stamped schema rows (incl. gap rows). Async feed
collectors (Task 8) and --dry-run both go through build_rows."""
import json
from dataclasses import dataclass

from edgelab import parse
from edgelab.clockstamp import ClockStamper
from edgelab.seqgap import SeqGapTracker

_PARSERS = {
    "binance": parse.parse_binance,
    "coinbase": parse.parse_coinbase,
    "pm_oracle": parse.parse_pm_oracle,
    "pm_clob": parse.parse_pm_clob,
}

ORACLE_SYMBOL = {
    "btcusdt": "BTC", "ethusdt": "ETH", "solusdt": "SOL",
    "xrpusdt": "XRP", "dogeusdt": "DOGE", "bnbusdt": "BNB",
}


@dataclass
class BuildCtx:
    stamper: ClockStamper
    gaps: SeqGapTracker
    symbol: str
    symbols: set
    token_index: dict   # asset_id -> (symbol, window_slug)


def build_rows(family: str, raw, ctx: BuildCtx) -> list:
    msg = json.loads(raw) if isinstance(raw, str) else raw
    raw_str = raw if isinstance(raw, str) else json.dumps(msg, separators=(",", ":"))
    rows = []
    for p in _PARSERS[family](msg):
        p = dict(p)
        source = p.pop("source")
        symbol, window_slug = ctx.symbol, None
        if family == "pm_oracle":
            sym = ORACLE_SYMBOL.get(p.pop("symbol_raw", None))
            if sym is None or sym not in ctx.symbols:
                continue
            symbol = sym
        elif family == "pm_clob":
            sw = ctx.token_index.get(p.pop("asset_id", None))
            if sw is None:
                continue
            symbol, window_slug = sw
        seq = p.get("exch_seq")
        gap = ctx.gaps.check(source, seq) if seq is not None else None
        if gap:
            rows.append(ctx.stamper.stamp(
                "gap", symbol=symbol, window_slug=window_slug,
                exch_seq=gap["gap_end"],
                payload_json=json.dumps(gap, separators=(",", ":"))))
        rows.append(ctx.stamper.stamp(
            source, symbol=symbol, window_slug=window_slug,
            payload_json=raw_str, **p))
    return rows


# ---------------------------------------------------------------------------
# Async feed collectors (Task 8) — appended below build_rows
# ---------------------------------------------------------------------------
import asyncio
import time

try:
    import websockets
except Exception:
    websockets = None

from edgelab import windows
from edgelab.probes import probe_clob, probe_rpc, probe_tls

CLOB_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


def _session_row(ctx, feed, event, **extra):
    return ctx.stamper.stamp("session", payload_json=json.dumps(
        {"feed": feed, "event": event, **extra}, separators=(",", ":")))


async def _pump(ws, family, ctx, writer):
    raw = await asyncio.wait_for(ws.recv(), timeout=30)
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if not raw.strip():            # empty keepalive frame (PM oracle sends one on connect)
        return
    msgs = json.loads(raw)
    for m in (msgs if isinstance(msgs, list) else [msgs]):
        for row in build_rows(family, m, ctx):
            writer.write(row)


async def run_feed(name, url, sub, family, ctx, writer, stop, log):
    backoff = 1
    while not stop.is_set():
        try:
            async with websockets.connect(url, ping_interval=10, close_timeout=3,
                                          compression=None, max_size=2 ** 21) as ws:
                if sub is not None:
                    await ws.send(json.dumps(sub))
                writer.write(_session_row(ctx, name, "connect"))
                backoff = 1
                while not stop.is_set():
                    await _pump(ws, family, ctx, writer)
        except Exception as e:
            writer.write(_session_row(ctx, name, "disconnect",
                                      err=f"{type(e).__name__}:{e}"))
            log(f"{name} reconnect in {min(backoff,30)}s: {type(e).__name__}")
            await asyncio.sleep(min(backoff, 30))
            backoff *= 2


async def run_pm_clob(ctx, writer, stop, log, horizons, refresh_s=300):
    while not stop.is_set():
        wins = windows.current_windows(ctx.symbol, horizons)
        ctx.token_index.clear()
        tokens = []
        for w in wins:
            ctx.token_index[w["up_token"]] = (w["symbol"], w["slug"])
            ctx.token_index[w["down_token"]] = (w["symbol"], w["slug"])
            tokens += [w["up_token"], w["down_token"]]
        if not tokens:
            await asyncio.sleep(5)
            continue
        try:
            async with websockets.connect(CLOB_WS, ping_interval=10, close_timeout=3,
                                          compression=None, max_size=2 ** 21) as ws:
                await ws.send(json.dumps({"type": "market", "assets_ids": tokens}))
                writer.write(_session_row(ctx, "pm_clob", "connect", n_tokens=len(tokens)))
                t0 = time.time()
                while not stop.is_set() and time.time() - t0 < refresh_s:
                    await _pump(ws, "pm_clob", ctx, writer)
        except Exception as e:
            writer.write(_session_row(ctx, "pm_clob", "disconnect",
                                      err=f"{type(e).__name__}:{e}"))
            log(f"pm_clob reconnect: {type(e).__name__}")
            await asyncio.sleep(3)


async def run_probes(rpc_url, tls_hosts, ctx, writer, stop, period_s=2.0,
                     clob_url=None):
    while not stop.is_set():
        if clob_url:  # binding-constraint submit leg (CLOB matching engine)
            crtt = await probe_clob(clob_url)
            if crtt is not None:
                writer.write(ctx.stamper.stamp("probe_clob", rtt_ns=crtt, symbol=None))
        rtt = await probe_rpc(rpc_url)  # secondary: settlement leg only
        if rtt is not None:
            writer.write(ctx.stamper.stamp("probe_rpc", rtt_ns=rtt, symbol=None))
        for host, port in tls_hosts:
            t = await probe_tls(host, int(port))
            if t is not None:
                writer.write(ctx.stamper.stamp("probe_tls", rtt_ns=t, symbol=None))
        await asyncio.sleep(period_s)
