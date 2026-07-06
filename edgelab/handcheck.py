"""Look-ahead / reconstruction audit — run BEFORE believing any surviving decay
curve. For a window's Parquet it (1) independently recomputes the CKS OFI from
the logged best quotes and compares it to the stored ofi_inc (catches a book
reconstruction bug), and (2) prints, for a sample of rows, the exact
source-t -> target-(t+lag) pairing with timestamps and gaps so you can confirm
by eye that the move never bleeds backward in time.

Usage:
  python3 -m edgelab.handcheck <window.parquet> [--lag-ms 250] [--n 6] [--side up]
"""

import argparse

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from edgelab.ofi import ofi_increment


def audit(path, lag_ms=250, n=6, side="up"):
    df = pq.read_table(path).to_pandas()
    # OFI recompute MUST use file/emission order (the order the recorder's
    # accumulator saw states). Sorting by ts would reorder rows sharing an
    # identical recv_ts and break the consecutive-state differencing.
    g = df[df["side"] == side].reset_index(drop=True)
    print(f"\n=== {path}  side={side}  rows={len(g)}  lag={lag_ms}ms ===")

    # (1) independent OFI recompute vs stored ofi_inc
    def _p(x):   # Parquet stores an empty book side's price as NaN; the recorder
        return None if (x is None or (isinstance(x, float) and np.isnan(x))) else float(x)
    bad = 0
    prev = None
    recomputed = np.zeros(len(g))
    for i, r in g.iterrows():
        # ...used None there (-> 0 flow). Mirror that exactly or the audit lies.
        cur = (_p(r.best_bid), float(r.best_bid_sz or 0.0),
               _p(r.best_ask), float(r.best_ask_sz or 0.0))
        recomputed[i] = ofi_increment(prev, cur)
        prev = cur
    diff = np.abs(recomputed - g["ofi_inc"].to_numpy())
    bad = int((diff > 1e-6).sum())
    print(f"[OFI recompute] max|Δ| vs stored ofi_inc = {diff.max():.3e} ; "
          f"mismatched rows = {bad}/{len(g)}  -> {'OK' if bad == 0 else 'MISMATCH (reconstruction bug!)'}")

    # (2) explicit t -> t+lag pairing audit on a sample (ts-sorted, STABLE so
    # ties keep emission order — mirrors decay.decay_curve's per-window view)
    g = g.sort_values("ts", kind="stable").reset_index(drop=True)
    ts = g["ts"].to_numpy(dtype=float)
    mid = g["mid"].to_numpy(dtype=float)
    lag_s = lag_ms / 1000.0
    j = np.searchsorted(ts, ts + lag_s, side="left")
    print(f"\n[pairing] showing {n} sample rows (source t -> target t+lag):")
    print(f"{'i':>5} {'src_ts':>14} {'ofi_inc':>9} {'src_mid':>8} | "
          f"{'j':>5} {'tgt_ts':>14} {'gap_ms':>7} {'tgt_mid':>8} {'move':>9} {'fwd?':>5}")
    sample = np.linspace(0, max(0, len(g) - 2), num=min(n, len(g)), dtype=int)
    for i in sample:
        if j[i] >= len(g):
            print(f"{i:>5} {ts[i]:>14.3f} {g['ofi_inc'][i]:>9.2f} {mid[i]:>8.4f} |   (no target within lag)")
            continue
        gap = (ts[j[i]] - ts[i]) * 1000
        fwd = "yes" if (j[i] > i and ts[j[i]] >= ts[i] + lag_s - 1e-12) else "LEAK"
        print(f"{i:>5} {ts[i]:>14.3f} {g['ofi_inc'][i]:>9.2f} {mid[i]:>8.4f} | "
              f"{j[i]:>5} {ts[j[i]]:>14.3f} {gap:>7.1f} {mid[j[i]]:>8.4f} "
              f"{mid[j[i]] - mid[i]:>9.4f} {fwd:>5}")
    leaks = int(np.sum((j < len(g)) & ~((j > np.arange(len(g))) &
                                        (ts[np.clip(j, 0, len(g) - 1)] >= ts + lag_s - 1e-12))))
    print(f"\n[pairing] backward/contemporaneous pairings detected: {leaks}  "
          f"-> {'OK (strictly forward)' if leaks == 0 else 'LOOK-AHEAD LEAK'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("parquet")
    ap.add_argument("--lag-ms", type=int, default=250)
    ap.add_argument("--n", type=int, default=6)
    ap.add_argument("--side", default="up")
    a = ap.parse_args()
    audit(a.parquet, a.lag_ms, a.n, a.side)
