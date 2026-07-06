"""Edge-B step-2 backtest: scalp the OFI->next-tick signal against the logged
bid/ask spread.

Strategy A (the honest monetization of the decay signal): within a window, when
the OFI increment on a token exceeds theta, BUY that token at the logged ask and
sell it back at the logged bid at the first event >= entry_ts + lag. PnL pays the
full round-trip spread. Trades are NON-OVERLAPPING (the next entry is only scanned
after the prior exit) so they are ~independent, and the exit is taken STRICTLY
after entry_ts+lag (no look-ahead, same window only).
"""

import numpy as np


def scalp_window(g, theta, lag_ms):
    """Non-overlapping long-only scalps for one window/side. `g` is one window's
    rows for one token side. Returns a list of trade dicts."""
    g = g.sort_values("ts", kind="stable")
    ts = g["ts"].to_numpy(dtype=float)
    ofi = g["ofi_inc"].to_numpy(dtype=float)
    ask = g["best_ask"].to_numpy(dtype=float)
    bid = g["best_bid"].to_numpy(dtype=float)
    n = len(ts)
    lag_s = lag_ms / 1000.0
    if lag_s <= 0:
        raise ValueError("lag must be positive (no contemporaneous exit)")
    # vectorized exit index for every event; the non-overlap selection then only
    # iterates the (sparse) trigger events, not every book update.
    j_all = np.searchsorted(ts, ts + lag_s, side="left")
    triggers = np.flatnonzero((ofi > theta) & ~np.isnan(ask))
    trades = []
    cursor = -1  # last exit index; next entry must be strictly after it
    for i in triggers:
        if i <= cursor:
            continue  # inside an already-open trade -> non-overlapping
        j = int(j_all[i])
        if j >= n:
            break  # no exit within window; later triggers (later ts) also fail
        assert j > i and ts[j] >= ts[i] + lag_s - 1e-12, "look-ahead at exit seam"
        if np.isnan(bid[j]):
            continue
        trades.append({
            "entry_ts": float(ts[i]), "entry_ask": float(ask[i]),
            "exit_ts": float(ts[j]), "exit_bid": float(bid[j]),
            "pnl": float(bid[j] - ask[i]),
        })
        cursor = j
    return trades


def scalp_trades(df, theta, lag_ms, side="up"):
    """Pool non-overlapping scalps across all windows for one token side."""
    d = df[df["side"] == side] if "side" in df.columns else df
    trades = []
    for _, g in d.groupby("slug", sort=False):
        for t in scalp_window(g, theta, lag_ms):
            t = dict(t)
            t["slug"] = g["slug"].iloc[0]
            trades.append(t)
    return trades
