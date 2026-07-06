"""Load a capture directory into numpy series for the capturability sim, and
resolve market outcomes. Read-only; never mutates capture data."""
import glob
import json
import os

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq


def _read(data_dir, src, cols):
    fs = sorted(glob.glob(os.path.join(data_dir, "events", "day=*",
                                       f"source={src}", "*.parquet")))
    if not fs:
        return {c: np.array([]) for c in cols}
    t = pa.concat_tables([pq.read_table(f, partitioning=None, columns=cols) for f in fs])
    return {c: np.array(t.column(c).to_pylist()) for c in cols}


def _nan(arr):
    return np.array([np.nan if x is None else float(x) for x in arr], dtype=float)


def load_capture(data_dir):
    b = _read(data_dir, "binance_bookticker", ["recv_wall_ns", "best_bid", "best_ask"])
    bt = np.asarray(b["recv_wall_ns"], float) / 1e9
    bmid = (_nan(b["best_bid"]) + _nan(b["best_ask"])) / 2.0
    o = np.argsort(bt); bt, bmid = bt[o], bmid[o]

    c = _read(data_dir, "pm_clob_book",
              ["recv_wall_ns", "best_bid", "best_ask", "best_ask_sz", "payload_json"])
    assets = {}
    if len(c["recv_wall_ns"]):
        parsed = [json.loads(p) for p in c["payload_json"]]
        aid = np.array([d["asset_id"] for d in parsed])
        cond = np.array([d.get("market") for d in parsed], dtype=object)
        ct = np.asarray(c["recv_wall_ns"], float) / 1e9
        ask = _nan(c["best_ask"]); asz = _nan(c["best_ask_sz"])
        mid = (_nan(c["best_bid"]) + _nan(c["best_ask"])) / 2.0
        for a in set(aid):
            m = aid == a
            idx = np.argsort(ct[m])
            # condition_id is stable per asset; take any non-empty one
            conds = [x for x in cond[m] if x]
            assets[a] = {"t": ct[m][idx], "ask": ask[m][idx],
                         "ask_sz": asz[m][idx], "mid": mid[m][idx],
                         "condition_id": conds[0] if conds else None}

    orc = _read(data_dir, "pm_oracle", ["recv_wall_ns", "price"])
    ot = np.asarray(orc["recv_wall_ns"], float) / 1e9
    oval = _nan(orc["price"])
    oo = np.argsort(ot); ot, oval = ot[oo], oval[oo]
    return {"bt": bt, "bmid": bmid, "assets": assets, "ot": ot, "oval": oval}


def _clob_outcome(asset_id, condition_id):
    """Resolve via the CLOB market endpoint by condition_id (the reliable source;
    Gamma's token/condition filters do not work). Return 1 if this token is the
    winner, 0 if it lost, None if the market is not cleanly closed, the token is
    absent, or condition_id is missing. Network errors -> None (never a guess)."""
    import requests
    if not condition_id:
        return None
    r = requests.get(f"https://clob.polymarket.com/markets/{condition_id}", timeout=10)
    r.raise_for_status()
    m = r.json()
    if not m.get("closed"):
        return None
    for tk in m.get("tokens", []):
        if str(tk.get("token_id")) == str(asset_id):
            w = tk.get("winner")
            if w is True:
                return 1
            if w is False:
                return 0
            return None                   # winner not yet stamped
    return None                           # our token not in this market


def resolve_outcomes(conditions, fetcher=None):
    """Resolve market outcomes. `conditions` is a dict asset_id -> condition_id.
    Returns dict of resolved asset_id -> 1 (won) or 0 (lost). Unresolved/unknown
    IDs are omitted (so callers can count n_resolved).

    fetcher(asset_id, condition_id) -> int | None: injectable for tests; None uses
    _clob_outcome. Any exception from fetcher is swallowed into omission (None)."""
    fetcher = fetcher or _clob_outcome
    out = {}
    for a, cond in conditions.items():
        try:
            v = fetcher(a, cond)
        except Exception:
            v = None
        if v is not None:
            out[a] = int(v)
    return out
