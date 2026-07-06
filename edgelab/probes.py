"""Latency probes (first-class data stream).

probe_clob is the BINDING-CONSTRAINT measurement: edge-capturing orders are
placed via a signed POST to the CLOB matching engine (clob.polymarket.com), so
the round-trip to that host is the leg that actually gates fills. We cannot POST
an order (record-only), so probe_clob times a read-only GET /time round-trip to
the same host as a LOWER BOUND on real order-placement RTT (it omits request
signing, body size, and operator-side matching, so true submit time is worse).

probe_rpc is a SECONDARY signal only: the Polygon RPC handles on-chain
SETTLEMENT (redeem/approve/balance), which happens AFTER the fill and does not
gate capture. It is a deliberate lower bound on the settlement leg, not submit.
Never interpret probe_rpc as submission latency."""
import asyncio
import time


def rpc_payload() -> dict:
    return {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}


def parse_rpc_block(resp: dict) -> int:
    if not isinstance(resp, dict) or "error" in resp or "result" not in resp:
        raise ValueError(f"bad rpc response: {resp}")
    return int(resp["result"], 16)


async def timed_call(async_fn):
    t0 = time.monotonic_ns()
    res = await async_fn()
    return time.monotonic_ns() - t0, res


async def probe_rpc(url, caller=None, timeout: float = 5.0):
    """Time one eth_blockNumber round-trip. `caller` (sync, returns block int) is
    injectable for tests; default uses requests in a thread executor."""
    if caller is None:
        def caller():
            import requests
            r = requests.post(url, json=rpc_payload(), timeout=timeout)
            return parse_rpc_block(r.json())

    async def run():
        return await asyncio.get_running_loop().run_in_executor(None, caller)
    try:
        rtt_ns, _ = await asyncio.wait_for(timed_call(run), timeout=timeout + 1.0)
        return rtt_ns
    except Exception:
        return None


def clob_time_url(base: str) -> str:
    """The read-only health endpoint on the CLOB host used as the submit-leg
    lower bound. `base` is the CLOB host root (e.g. https://clob.polymarket.com)."""
    return base.rstrip("/") + "/time"


def parse_clob_time(text) -> int:
    """The CLOB /time endpoint returns the server epoch seconds as a bare integer
    in the body. Raise if the body is not a clean integer (HTML error page, etc.)."""
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        raise ValueError(f"bad clob /time response: {text!r}")


async def probe_clob(url, caller=None, timeout: float = 5.0):
    """Time one read-only GET /time round-trip to the CLOB host — the
    binding-constraint LOWER BOUND on order-placement RTT. `caller` (sync,
    returns the parsed server-time int) is injectable for tests; default uses
    requests in a thread executor. `url` is the CLOB host root."""
    if caller is None:
        target = clob_time_url(url)

        def caller():
            import requests
            r = requests.get(target, timeout=timeout)
            return parse_clob_time(r.text)

    async def run():
        return await asyncio.get_running_loop().run_in_executor(None, caller)
    try:
        rtt_ns, _ = await asyncio.wait_for(timed_call(run), timeout=timeout + 1.0)
        return rtt_ns
    except Exception:
        return None


async def probe_tls(host, port: int = 443, timeout: float = 5.0):
    """Time one TCP+TLS handshake to host:port (network-layer lower bound)."""
    import ssl
    sslctx = ssl.create_default_context()

    async def run():
        reader, writer = await asyncio.open_connection(
            host, port, ssl=sslctx, server_hostname=host)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    try:
        rtt_ns, _ = await asyncio.wait_for(timed_call(run), timeout=timeout)
        return rtt_ns
    except Exception:
        return None
