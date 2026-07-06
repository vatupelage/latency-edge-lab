"""OFI-decay curve — the cheap Edge-B gate.

Before building any strategy layer, measure how fast the Up-token
OFI -> next-move relationship decays with lag. If the signal is gone by the
time our RTT (~1 ms co-located) lets us act, Edge B is dead and we've spent one
plot instead of a strategy. If it survives, the full B test is justified. The
per-binary decay shape is itself a reusable fact about this market.

Method, per lag L (ms):
  predictor s_t  = OFI increment at event t (configurable column)
  target    r_t  = mid(t + L) - mid(t), using the FIRST event strictly after
                   t+L within the SAME window (slug). No look-ahead, no
                   cross-window pairing.
  power          = Pearson corr(s, r), its two-sided t-stat, sign accuracy.
  mde_corr       = minimum |corr| detectable at this n (alpha .05, power .80)
                   via the Fisher-z approximation = (z_a/2 + z_b)/sqrt(n-3).
                   So "no signal" means "|corr| < mde_corr", not "we found none".
"""

import numpy as np

_Z_ALPHA_2 = 1.959963985   # two-sided 0.05
_Z_BETA = 0.841621234      # power 0.80


def microprice(bid, bid_sz, ask, ask_sz):
    """Size-weighted micro-price = (bid*ask_sz + ask*bid_sz)/(bid_sz+ask_sz).
    Heavier bid depth pulls it toward the ask (buy pressure). Falls back to the
    mid when sizes are absent."""
    bid = np.asarray(bid, dtype=float); ask = np.asarray(ask, dtype=float)
    bsz = np.asarray(bid_sz, dtype=float); asz = np.asarray(ask_sz, dtype=float)
    tot = bsz + asz
    mid = (bid + ask) / 2.0
    with np.errstate(invalid="ignore", divide="ignore"):
        micro = np.where(tot > 0, (bid * asz + ask * bsz) / tot, mid)
    return micro[()] if micro.ndim == 0 else micro


def _target_series(g, target):
    """The pre-registered 'move' price series for one window. `target` is fixed
    BEFORE seeing data; testing more than one target = more trials for PBO."""
    if target == "mid":
        return g["mid"].to_numpy(dtype=float)
    if target == "microprice":
        return microprice(g["best_bid"].to_numpy(dtype=float),
                          g["best_bid_sz"].to_numpy(dtype=float),
                          g["best_ask"].to_numpy(dtype=float),
                          g["best_ask_sz"].to_numpy(dtype=float))
    raise ValueError(f"unknown move target: {target!r} (use 'mid' or 'microprice')")


def _forward_returns(ts, px, lag_s):
    """For each i, px[j]-px[i] where j is the FIRST index with ts[j] >= ts[i]+lag_s
    (strictly later than t for lag_s>0), else NaN. ts must be sorted ascending.

    Look-ahead guard: the target price is taken strictly after t+lag, so a row's
    OFI-at-t is never paired with a contemporaneous-or-earlier price. Asserted
    explicitly — this is the timing seam where leakage hides."""
    if lag_s <= 0:
        raise ValueError("lag must be positive (no contemporaneous pairing)")
    j = np.searchsorted(ts, ts + lag_s, side="left")
    out = np.full(len(ts), np.nan)
    valid = j < len(ts)
    vi = np.nonzero(valid)[0]
    if len(vi):
        # guard: every paired target is strictly later in time than its source
        assert np.all(ts[j[vi]] >= ts[vi] + lag_s - 1e-12), "look-ahead: target earlier than t+lag"
        assert np.all(j[vi] > vi), "look-ahead: target index not strictly forward"
    out[valid] = px[j[valid]] - px[valid]
    return out


def decay_curve(df, lags_ms, predictor="ofi_inc", side="up", target="mid"):
    """Predictive power of `predictor` (default OFI increment) over the move in
    the pre-registered `target` price ('mid' | 'microprice') at each lag.
    Returns a list of dicts: lag_ms, n, corr, abs_corr, t_stat, sign_acc,
    mde_corr, target."""
    d = df
    if "side" in d.columns:
        d = d[d["side"] == side]
    results = []
    for lag_ms in lags_ms:
        lag_s = lag_ms / 1000.0
        preds, rets = [], []
        for _, g in d.groupby("slug", sort=False):
            g = g.sort_values("ts", kind="stable")
            ts = g["ts"].to_numpy(dtype=float)
            px = _target_series(g, target)
            s = g[predictor].to_numpy(dtype=float)
            fr = _forward_returns(ts, px, lag_s)
            ok = ~np.isnan(fr)
            preds.append(s[ok]); rets.append(fr[ok])
        s = np.concatenate(preds) if preds else np.array([])
        r = np.concatenate(rets) if rets else np.array([])
        res = _power(lag_ms, s, r)
        res["target"] = target
        results.append(res)
    return results


def decay_by_regime(df, lags_ms, regime, **kw):
    """Run `decay_curve` separately per liquidity regime instead of one blended
    line (a pooled curve can hide a thin-book signal under the average, or fake
    one). `regime` is a per-row label Series aligned to df.index. Returns
    {regime_label: curve}."""
    out = {}
    for label, idx in df.groupby(regime).groups.items():
        out[label] = decay_curve(df.loc[idx], lags_ms, **kw)
    return out


def session_regime(df):
    """Default liquidity regime by UTC hour: 'liquid' during ~US hours
    (13:00-21:00 UTC) else 'thin'. Returns a label Series aligned to df.index."""
    import pandas as pd
    hr = pd.to_datetime(df["ts"], unit="s", utc=True).dt.hour
    return hr.between(13, 20).map({True: "liquid", False: "thin"})


def decay_verdict(curve, rtt_ms):
    """Mechanical Edge-B gate verdict, CITING THE MDE. Picks the curve bucket at
    (or just past) our acting latency rtt_ms and declares the signal alive only
    if its surviving |corr| EXCEEDS the minimum detectable correlation there.
    'Dead at RTT' therefore means 'smaller than we could detect', never 'the
    line looks low'."""
    usable = [c for c in curve if c["lag_ms"] >= rtt_ms] or curve
    c = min(usable, key=lambda x: x["lag_ms"])
    ac = c["abs_corr"]
    mde = c.get("mde_corr", float("nan"))
    sig = c.get("t_stat", 0.0)
    alive = (ac > mde) and (sig >= 2.0)
    if alive:
        stmt = (f"ALIVE at RTT≈{rtt_ms}ms (lag {c['lag_ms']}ms): |corr|={ac:.3f} "
                f"> MDE {mde:.3f}, t={sig:.1f}, n={c['n']}.")
    else:
        stmt = (f"DEAD at RTT≈{rtt_ms}ms (lag {c['lag_ms']}ms): |corr|={ac:.3f} "
                f"≤ MDE {mde:.3f} (smallest detectable at n={c['n']}); "
                f"t={sig:.1f}. Not 'looks low' — below what we could detect.")
    return {"rtt_bucket_ms": c["lag_ms"], "abs_corr": ac, "mde_corr": mde,
            "t_stat": sig, "n": c["n"], "alive": bool(alive), "statement": stmt}


def _power(lag_ms, s, r):
    n = len(s)
    if n < 5 or np.std(s) == 0 or np.std(r) == 0:
        return {"lag_ms": lag_ms, "n": int(n), "corr": 0.0, "abs_corr": 0.0,
                "t_stat": 0.0, "sign_acc": float("nan"), "mde_corr": float("nan")}
    corr = float(np.corrcoef(s, r)[0, 1])
    corr = 0.0 if np.isnan(corr) else corr
    denom = max(1e-12, 1.0 - corr * corr)
    t_stat = abs(corr) * np.sqrt(max(0, n - 2) / denom)
    sign_acc = float(np.mean(np.sign(s) == np.sign(r)))
    mde_corr = (_Z_ALPHA_2 + _Z_BETA) / np.sqrt(max(1, n - 3))
    return {"lag_ms": lag_ms, "n": int(n), "corr": round(corr, 5),
            "abs_corr": round(abs(corr), 5), "t_stat": round(float(t_stat), 3),
            "sign_acc": round(sign_acc, 4), "mde_corr": round(float(mde_corr), 5)}
