# predictor/edgelab/windows.py
"""Gamma-backed current-window discovery for the CLOB collector. Reuses
edgelab.logger.resolve_tokens (keyless Gamma)."""
import time

from edgelab.logger import resolve_tokens


def current_windows(symbol: str = "BTC",
                    horizons=(("5m", 300), ("15m", 900))) -> list:
    sym = symbol.lower()
    now = int(time.time())
    out = []
    for hz, period in horizons:
        start = (now // period) * period
        slug = f"{sym}-updown-{hz}-{start}"
        toks = resolve_tokens(slug)
        if toks is None:
            continue
        up, down = toks
        out.append({"slug": slug, "horizon": hz, "up_token": up,
                    "down_token": down, "symbol": symbol,
                    "close_ts": start + period})
    return out
