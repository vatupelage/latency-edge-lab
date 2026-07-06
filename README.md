# edgelab — research harness for two solo-viable Polymarket BTC Up/Down edges

**Measurement tool, NOT a live trader.** Goal: honestly measure whether either
edge exists before risking a dollar. Biased toward correctly reporting "no
edge". See `ACCESS_NOTES.md` (Phase 0) and `REPORT.md` (the deliverable).

## Edges under test
- **A — middle-of-window fair value** vs the lagging book (StudentT digital off
  a live vol estimate). Latency-gated like arb; **glance-only**, and on a CEX
  PROXY any positive result is presumptively a proxy↔Chainlink-basis / σ-error
  artifact (see ACCESS_NOTES).
- **B — binary OFI** (Cont-Kukanov-Stoikov) on the Up-token CLOB, predicting the
  binary's OWN next move, independent of BTC. The one orthogonal, untested edge.

## Modules
| file | role | tests |
|---|---|---|
| `bookstate.py` | both-side L2 book reconstruction from WS snapshot+diffs | ✅ |
| `ofi.py` | CKS level-1 OFI from consecutive best quotes | ✅ |
| `recorder.py` | WS messages → top-of-book rows + per-token OFI | ✅ |
| `logger.py` | asyncio WS capture (5m+15m, both tokens), Parquet per window | (replay-validated) |
| `replay.py` | synthetic windows w/ known decaying signal (pipeline validation) | ✅ via decay |
| `decay.py` | **OFI-decay curve — the cheap B gate** | ✅ |
| `eval.py` | edge/trade + CI + **MDE**, Deflated Sharpe, PBO | ✅ |

## Run the logger (forward; we have no history)
```bash
EDGELAB_OUT=/mnt/data/edgelab_data python3 -m edgelab.logger
```
Writes `events/day=YYYY-MM-DD/horizon={5m,15m}/<slug>.parquet` (one immutable
file per closed window → restart-safe) + `windows.jsonl` metadata.

## Validate the analysis pipeline before real data
```bash
python3 -m edgelab.replay /tmp/edgelab_replay     # synthetic dataset
pytest test_edgelab_*.py -q                        # 34 tests
```

## The gate-first workflow (do NOT skip)
1. Collect a few days forward.
2. Run the **OFI-decay curve**. If predictive power is gone by our RTT
   (~1 ms co-located), **Edge B is dead** — one plot, no strategy built.
3. Only if the signal survives the RTT: build the B strategy backtest (fills
   against logged bid/ask, shrinkage ON, segmented), and report with DSR/PBO.
4. Every "no edge" verdict cites the **minimum detectable edge** at the achieved
   N, so it means "no edge larger than X¢", never "we didn't find one".
