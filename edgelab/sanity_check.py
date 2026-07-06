"""Milestone-1 sanity gate: read the captured Parquet and assert all five feed
families produced rows, every row carries clock_err_ns, and all three probe RTT
distributions (binding-constraint probe_clob + secondary probe_rpc/probe_tls)
are populated. NOT an edge test — just 'is the capture healthy'."""
import glob
import os
import sys

import numpy as np
import pyarrow.parquet as pq


def _percentiles(vals):
    if not vals:
        return {"p50": None, "p90": None, "p99": None, "n": 0}
    a = np.array(vals, dtype="float64")
    return {"p50": int(np.percentile(a, 50)),
            "p90": int(np.percentile(a, 90)),
            "p99": int(np.percentile(a, 99)), "n": len(vals)}


def summarize(data_dir: str) -> dict:
    files = glob.glob(os.path.join(data_dir, "events", "day=*", "source=*", "*.parquet"))
    by_source, clock_nulls = {}, 0
    rtts = {"probe_clob": [], "probe_rpc": [], "probe_tls": []}
    total = 0
    for path in files:
        # partitioning=None: files sit under hive-style day=/source= dirs AND
        # carry their own `source` column; without this pyarrow infers `source`
        # from the path and clashes types (ArrowTypeError). Read in-file cols only.
        tbl = pq.read_table(path, partitioning=None)
        total += tbl.num_rows
        srcs = tbl.column("source").to_pylist()
        errs = tbl.column("clock_err_ns").to_pylist()
        rtt_col = tbl.column("rtt_ns").to_pylist()
        for s, e, r in zip(srcs, errs, rtt_col):
            by_source[s] = by_source.get(s, 0) + 1
            if e is None:
                clock_nulls += 1
            if s in rtts and r is not None:
                rtts[s].append(r)
    return {"total_rows": total, "by_source": by_source,
            "clock_err_nulls": clock_nulls,
            "probe": {k: _percentiles(v) for k, v in rtts.items()}}


def check_gate(summary: dict) -> tuple:
    reasons = []
    bs = summary["by_source"]
    for family, prefix in (("binance", "binance_"), ("coinbase", "coinbase_"),
                           ("pm_oracle", "pm_oracle"), ("pm_clob", "pm_clob_")):
        if not any(s.startswith(prefix) and n > 0 for s, n in bs.items()):
            reasons.append(f"no rows for feed family: {family}")
    if summary["clock_err_nulls"] != 0:
        reasons.append(f"clock_err_ns null on {summary['clock_err_nulls']} rows")
    for probe in ("probe_clob", "probe_rpc", "probe_tls"):
        p = summary["probe"].get(probe, {})
        if not p.get("n") or p.get("p50") is None:
            reasons.append(f"{probe} distribution empty")
    return (len(reasons) == 0, reasons)


def main():
    data_dir = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("EDGELAB_OUT", ".")
    s = summarize(data_dir)
    ok, reasons = check_gate(s)
    print(f"total_rows={s['total_rows']}")
    for src, n in sorted(s["by_source"].items()):
        print(f"  {src}: {n}")
    print(f"clock_err_nulls={s['clock_err_nulls']}")
    for probe, p in s["probe"].items():
        print(f"  {probe}: {p}")
    print("GATE:", "PASS" if ok else "FAIL")
    for r in reasons:
        print("  -", r)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
