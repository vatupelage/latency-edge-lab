"""Pure simulation math for the fee-aware capturability kill test. No I/O.
All times are seconds in the capture's single recv_wall clock."""
import numpy as np

FEE_RATE = 0.07  # verified crypto taker rate, docs.polymarket.com/trading/fees


def bps_returns(t, mid, lookback_s):
    t = np.asarray(t, float); mid = np.asarray(mid, float)
    j = np.searchsorted(t, t - lookback_s, side="right") - 1
    out = np.full(len(t), np.nan)
    ok = j >= 0
    out[ok] = (mid[ok] / mid[j[ok]] - 1.0) * 1e4
    return out


def momentum_triggers(t, mid, theta_bps, lookback_s, cooldown_s):
    t = np.asarray(t, float)
    r = bps_returns(t, mid, lookback_s)
    out = []
    last = -np.inf
    for i in range(len(t)):
        if np.isnan(r[i]) or abs(r[i]) < theta_bps:
            continue
        if t[i] - last < cooldown_s:
            continue
        out.append((float(t[i]), 1 if r[i] > 0 else -1))
        last = t[i]
    return out


def random_triggers(t0, t1, n, seed):
    rng = np.random.default_rng(seed)
    ts = np.sort(rng.uniform(t0, t1, size=n))
    ds = rng.choice((-1, 1), size=n)
    return [(float(a), int(b)) for a, b in zip(ts, ds)]


def _ffill(et, ev, grid):
    et = np.asarray(et, float); ev = np.asarray(ev, float)
    idx = np.searchsorted(et, grid, side="right") - 1
    idx[idx < 0] = 0  # callers clip grid to the series overlap, so idx<0 is unreachable; clip is defensive
    return ev[idx]


def polarity_from_levels(ta, lvla, tb, lvlb, dt=0.1):
    ta = np.asarray(ta, float); tb = np.asarray(tb, float)
    if len(ta) < 2 or len(tb) < 2:
        return 0
    t0 = max(ta[0], tb[0]); t1 = min(ta[-1], tb[-1])
    if t1 - t0 < dt:
        return 0
    g = np.arange(t0, t1, dt)
    a = _ffill(ta, lvla, g); b = _ffill(tb, lvlb, g)
    if a.std() == 0 or b.std() == 0:
        return 0
    c = np.corrcoef(a, b)[0, 1]
    if not np.isfinite(c) or c == 0:
        return 0
    return 1 if c > 0 else -1


def fee_per_share(price, rate=FEE_RATE):
    return rate * price * (1.0 - price)


def scalar_ffill(et, ev, q):
    et = np.asarray(et, float); ev = np.asarray(ev, float)
    i = int(np.searchsorted(et, q, side="right")) - 1
    if i < 0:
        return np.nan
    v = float(ev[i])
    return np.nan if np.isnan(v) else v


def hittable_ask(ta, ask, ask_sz, arrival, stake):
    px = scalar_ffill(ta, ask, arrival)
    if not np.isfinite(px) or px <= 0:
        return (np.nan, 0.0)
    size = scalar_ffill(ta, ask_sz, arrival)
    if not np.isfinite(size) or size <= 0:
        return (np.nan, 0.0)
    shares = min(stake / px, size)
    return (px, shares)


def mark_value(tm, mid, arrival, H):
    return scalar_ffill(tm, mid, arrival + H)


def net_edge_per_share(value, ask, rate=FEE_RATE):
    # entry-only fee on the buy; used identically for resolution and mark valuations
    return value - ask - fee_per_share(ask, rate)
