"""Replay mode — synthetic windows for validating the analysis pipeline before
real data accumulates (spec Phase 1 requirement).

`synth_window_rows` embeds a KNOWN, decaying OFI->future-move relationship: an
order-flow kick x[t] moves the mid one step later (single-step impact) plus iid
noise. So corr(OFI_now, return-over-next-L) is high at short lag and decays as
return-noise accumulates with L — exactly the shape the decay curve must
recover. `make_replay_dataset` writes these as Parquet in the live logger's
schema so decay.py / eval.py run end-to-end on synthetic ground truth.
"""

import numpy as np

from edgelab.logger import write_window
from edgelab.recorder import WindowRecorder


def synth_window_rows(slug, horizon, open_ts, *, n=2000, dt=0.05,
                      alpha=0.004, noise=0.004, seed=0, signal=True):
    """Up-token rows with an embedded single-step OFI impact.

    alpha: mid impact per unit flow, realized one step later.
    noise: iid per-step mid noise. signal=False -> pure noise (null control).
    Returns a list of row dicts in the WindowRecorder schema.
    """
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(n)                      # per-step OFI increment
    dm = np.zeros(n)
    eps = noise * rng.standard_normal(n)
    if signal:
        dm[1:] = alpha * x[:-1]                      # kick lands one step later
    dm += eps
    mid = 0.5 + np.cumsum(dm)
    mid = np.clip(mid, 0.02, 0.98)
    half = 0.005                                     # half-spread
    ofi_cum = np.cumsum(x)
    rows = []
    for t in range(n):
        m = float(mid[t])
        bb, ba = round(m - half, 4), round(m + half, 4)
        rows.append({
            "ts": open_ts + t * dt,
            "slug": slug, "horizon": horizon, "side": "up",
            "event_type": "price_change",
            "best_bid": bb, "best_bid_sz": 100.0,
            "best_ask": ba, "best_ask_sz": 100.0,
            "mid": m, "spread": round(ba - bb, 4),
            "ofi_inc": float(x[t]), "ofi_cum": float(ofi_cum[t]),
        })
    return rows


def make_replay_dataset(out_dir, n_windows=12, horizon="5m", period=300,
                        base_ts=1_700_000_000, signal=True, seed0=0):
    """Write `n_windows` synthetic windows as Parquet + metadata, mimicking the
    live logger output so the analysis modules can be validated offline."""
    paths = []
    for w in range(n_windows):
        open_ts = base_ts + w * period
        slug = f"btc-updown-{horizon}-{open_ts}"
        rows = synth_window_rows(slug, horizon, open_ts, seed=seed0 + w,
                                 signal=signal)
        rec = WindowRecorder(slug, horizon, "UP", "DOWN", open_ts, open_ts + period)
        rec.rows = rows
        # synthetic outcome: Up wins iff terminal mid > 0.5
        up_won = rows[-1]["mid"] > 0.5
        meta = rec.finalize(up_won=bool(up_won), terminal=rows[-1]["mid"],
                            feed="replay_synthetic")
        paths.append(write_window(rec, out_dir, meta))
    return paths


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "edgelab_replay"
    make_replay_dataset(out)
    print(f"wrote replay dataset -> {out}")
