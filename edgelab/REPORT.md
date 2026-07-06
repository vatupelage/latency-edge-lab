# REPORT.md — edge verdicts

> One page, per edge: does it clear costs out-of-sample, in which segments, at
> what (sub-linear) capacity? If it does not clear costs, say so directly. Every
> "no edge" cites the minimum detectable edge at the achieved N.

**Status: logger deployed 2026-06-11; verdict 2026-06-19 on ~3,000 windows / 9 days.**

## Edge B — Binary OFI — VERDICT: NO TRADEABLE EDGE

### Step 1 (gate): OFI-decay curve — PASSES (signal exists)
Up-token OFI does predict the binary's own next mid-move, surviving to RTT.
Leak-clean (handcheck: OFI recompute max|Δ|=0, zero backward pairings).

| lag | 5m \|corr\| | 15m \|corr\| |
|---|---|---|
| 5ms (≈RTT) | 0.031 | 0.032 |
| 250ms | 0.073 | 0.061 |
| 500ms | **0.078** | 0.068 |
| 1000ms | 0.069 | **0.068** |
| 2000ms | 0.055 | 0.064 |

Gate verdict: ALIVE at RTT (pooled + both liquid/thin regimes). t-stats 38–163
are large ONLY because n is millions; effect size is tiny (|corr| 0.03–0.08).

### Step 2 (strategy): scalp the signal vs the spread — FAILS
Long-only scalp: when OFI_inc > θ, buy at the logged ask, sell at the logged bid
at the first event ≥ entry+lag (pays the full round-trip spread). Non-overlapping
trades, no look-ahead (TDD'd in `scalp.py` / `test_edgelab_scalp.py`). Swept
θ ∈ {p50…p99 of positive OFI} × lag ∈ {250,500,1000}ms; deflated with DSR + PBO.

| horizon | best config | pnl/trade | 95% CI | n | DSR | PBO |
|---|---|---|---|---|---|---|
| 5m  | θ=118, 1000ms | **−0.640¢** | [−0.698, −0.582] | 11,303 | 0.000 | 0.000 |
| 15m | θ=299, 1000ms | **−0.630¢** | [−0.688, −0.582] | 5,766 | 0.000 | 0.000 |

EVERY cell of the sweep is negative (−0.63¢ to −1.06¢/trade), monotonically
less-bad as θ rises and lag lengthens (stronger signal, but still never crosses
zero). The loss ≈ the bid/ask spread (~1¢ on a ~$0.50 price). **The signal is
real but ~10–20× too small to pay the spread.** MDE at the best cells is
~0.08¢/trade — so "no edge larger than ~0.08¢/trade", not "we didn't find one".

### Sanity — OFI as a directional hold-to-resolution signal — no edge
One trade/window at the first OFI>θ event, held to resolution. Result is a
near-perfect ±40¢ MIRROR between up and down sides (5m: up −47.7¢ / down +46.6¢;
15m: up −39.9¢ / down +40.1¢), summing to ≈ −spread. That antisymmetry is the
signature of a price-LEVEL selection artifact (buy one side cheap, the other
expensive), not OFI predicting the 5–15min outcome. Consistent with the decay
curve fading by 2s: OFI carries no information about resolution.

## Conclusion
Edge B is the one genuinely non-dead signal in the project, but it is **not
tradeable**: the predictable move is far smaller than the spread, at every
threshold and horizon, deflation-confirmed. Do not build a live B strategy.
The only confirmed edge remains arbitrage (latency-gated).

## Edge A — middle-of-window fair value (GLANCE ONLY, PROXY) — not run
On a CEX proxy any positive result is presumptively a proxy↔Chainlink-basis /
σ-error artifact. Not evaluated; would require the real Chainlink feed to mean
anything.

## PRE-REGISTRATION (committed 2026-06-11, BEFORE any real data seen)
[unchanged — primary move target = mid; secondary = microprice counts as a 2nd
trial; RTT ~1ms; segmentation mandatory; handcheck before believing any curve.]
