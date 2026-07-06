"""Orchestrate the capturability sim: polarity, fills per (theta,R) cell, the
sweep with a random-entry null control, and the report/CLI. Read-only."""
import numpy as np

from edgelab import capsim


def asset_polarity(data, ntm=(0.05, 0.95), min_pts=50):
    out = {}
    for a, s in data["assets"].items():
        mid = s["mid"]
        ok = np.isfinite(mid) & (mid > ntm[0]) & (mid < ntm[1])
        if ok.sum() < min_pts:
            continue
        p = capsim.polarity_from_levels(data["bt"], data["bmid"], s["t"][ok], mid[ok])
        if p != 0:
            out[a] = p
    return out


def assemble_fills(data, polarity, triggers, R, stake, H, outcomes):
    fills = []
    for t, d in triggers:
        arrival = t + R
        for a, p in polarity.items():
            if p != d:
                continue
            s = data["assets"][a]
            ask, shares = capsim.hittable_ask(s["t"], s["ask"], s["ask_sz"], arrival, stake)
            if not np.isfinite(ask) or shares <= 0:
                continue
            mark = capsim.mark_value(s["t"], s["mid"], arrival, H)
            mark_net = capsim.net_edge_per_share(mark, ask) if np.isfinite(mark) else np.nan
            res_net = (capsim.net_edge_per_share(float(outcomes[a]), ask)
                       if a in outcomes else None)
            fills.append({"asset": a, "t": t, "arrival": arrival, "dir": d,
                          "ask": ask, "shares": shares,
                          "mark_net": mark_net, "res_net": res_net})
    return fills


def summarize(fills):
    """Summarize a list of fills.

    Returns dict with:
    - n: total fills
    - n_resolved: fills with non-None res_net
    - res_median: median of resolved res_net, or None if empty
    - res_frac_pos: fraction of resolved with res_net > 0, or None if empty
    - mark_median: median of non-NaN mark_net, or None if empty
    - median_ask: median of ask values
    """
    if not fills:
        return {"n": 0, "n_resolved": 0, "res_median": None, "res_frac_pos": None,
                "mark_median": None, "median_ask": None}

    res = [f["res_net"] for f in fills if f["res_net"] is not None]
    mark = [f["mark_net"] for f in fills if f["mark_net"] is not None
            and np.isfinite(f["mark_net"])]
    asks = [f["ask"] for f in fills]

    return {"n": len(fills), "n_resolved": len(res),
            "res_median": float(np.median(res)) if res else None,
            "res_frac_pos": float(np.mean([r > 0 for r in res])) if res else None,
            "mark_median": float(np.median(mark)) if mark else None,
            "median_ask": float(np.median(asks))}


def days_to_N(n_fills, span_s, N=30):
    """Calculate days to reach N fills given a fill rate.

    If n_fills <= 0 or span_s <= 0, returns inf.
    Otherwise: N / (fills_per_day), where fills_per_day = n_fills / (span_s / 86400).
    """
    if n_fills <= 0 or span_s <= 0:
        return float("inf")
    per_day = n_fills / (span_s / 86400.0)
    return N / per_day


def _fmt(x):
    """Format a number or None. Returns 'n/a' if None, else f'{x:+.4f}'."""
    return "n/a" if x is None else f"{x:+.4f}"


def run_sweep(data, polarity, outcomes, thetas, Rs, Hs, stake,
              lookback_s, cooldown_s, seed):
    """Run a sweep over (theta, R, H) cells with momentum + random control.

    For each (theta, R, H):
    - Compute momentum fills via capsim.momentum_triggers + assemble_fills
    - Compute random control with same fill count via capsim.random_triggers + assemble_fills
    - Summarize both; compute fee_bar = fee_per_share(median_ask) * 3 or None
    - Compute days_to_N for momentum fills

    Returns {"cells": [...], "span_s": ...} where each cell has keys:
    theta, R, H, momentum, random, fee_bar, days_to_N
    """
    bt = data["bt"]
    span_s = float(bt[-1] - bt[0]) if len(bt) > 1 else 0.0
    cells = []

    for theta in thetas:
        trg = capsim.momentum_triggers(bt, data["bmid"], theta, lookback_s, cooldown_s)
        for R in Rs:
            for H in Hs:
                mf = assemble_fills(data, polarity, trg, R, stake, H, outcomes)
                rnd = capsim.random_triggers(bt[0], bt[-1], max(len(trg), 1), seed)
                rf = assemble_fills(data, polarity, rnd, R, stake, H, outcomes)
                ms = summarize(mf)
                rs = summarize(rf)
                bar = (capsim.fee_per_share(ms["median_ask"]) * 3.0
                       if ms["median_ask"] is not None else None)
                cells.append({"theta": theta, "R": R, "H": H,
                              "momentum": ms, "random": rs, "fee_bar": bar,
                              "days_to_N": days_to_N(ms["n"], span_s)})

    return {"cells": cells, "span_s": span_s}


def print_report(report):
    """Print a resolution-led report. Outputs span_s, then per cell:
    n_resolved (honest sample size), then momentum res_median vs fee_bar,
    then momentum vs random mark_median (leak check), then days_to_N.
    """
    print(f"span_s={report['span_s']:.0f}")
    for c in report["cells"]:
        m = c["momentum"]
        r = c["random"]
        print(f"theta={c['theta']:g}bps R={c['R']*1000:.0f}ms H={c['H']:g}s | "
              f"n_resolved={m['n_resolved']} (N={m['n']}) | "
              f"res_median={_fmt(m['res_median'])} vs fee_bar={_fmt(c['fee_bar'])} | "
              f"mark mom={_fmt(m['mark_median'])} rnd={_fmt(r['mark_median'])} | "
              f"days_to_N={c['days_to_N']:.1f}")


def main():
    """CLI entry point. Loads capture data, computes polarity, resolves outcomes,
    runs sweep with default parameters, and prints report."""
    import os
    import sys
    from edgelab import capsim_io

    data_dir = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("EDGELAB_OUT", ".")
    data = capsim_io.load_capture(data_dir)
    polarity = asset_polarity(data)
    conditions = {a: data["assets"][a].get("condition_id") for a in polarity}
    outcomes = capsim_io.resolve_outcomes(conditions)
    # theta scaled to BTC's actual sub-second move distribution (a calm hour's
    # max 0.5s move was ~4bps); larger thresholds never fire.
    report = run_sweep(data, polarity, outcomes, thetas=[1.0, 2.0, 3.0],
                       Rs=[0.030, 0.060, 0.100, 0.150], Hs=[0.6, 1.0, 2.0], stake=5.0,
                       lookback_s=0.5, cooldown_s=0.5, seed=1)
    print(f"assets_with_polarity={len(polarity)} resolved={len(outcomes)}")
    print_report(report)


if __name__ == "__main__":
    main()
