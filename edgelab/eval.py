"""Honest-evaluation rig for the edge backtests.

The deliverable is an HONEST verdict, biased toward correctly reporting "no
edge". Every absence-of-edge result is paired with a minimum-detectable-edge so
it reads "no edge larger than X cents at n trades", not "we didn't find one".
Threshold sweeps are deflated (DSR + PBO) because sweeping manufactures edge.
"""

import math
from itertools import combinations

import numpy as np
from scipy import stats

_Z_ALPHA_2 = 1.959963985   # two-sided 0.05
_Z_BETA = 0.841621234      # power 0.80


def edge_per_trade(entry_prices, won, alpha=0.05):
    """Edge per trade for binaries bought at `entry_prices` (ask), paying 1 if
    `won` else 0. per-trade edge_i = won_i - entry_i (dollars per $1 share).
    Returns mean edge, std, se, t-stat, and a (1-alpha) CI. The headline number
    — realized win rate minus entry breakeven."""
    e = np.asarray(won, dtype=float) - np.asarray(entry_prices, dtype=float)
    n = len(e)
    mean = float(np.mean(e))
    sd = float(np.std(e, ddof=1)) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n > 0 else float("nan")
    t = mean / se if se > 0 else 0.0
    z = stats.norm.ppf(1 - alpha / 2)
    return {
        "n": int(n), "edge": mean, "sd": sd, "se": se, "t_stat": float(t),
        "ci_lo": mean - z * se if se > 0 else mean,
        "ci_hi": mean + z * se if se > 0 else mean,
        "mde": min_detectable_edge(n, sd) if sd > 0 else float("nan"),
    }


def min_detectable_edge(n, sd, alpha=0.05, power=0.80):
    """Smallest true per-trade edge (dollars/share) detectable at this n, given
    per-trade sd. = (z_{alpha/2} + z_{beta}) * sd / sqrt(n)."""
    if n <= 0 or sd <= 0:
        return float("nan")
    za = stats.norm.ppf(1 - alpha / 2)
    zb = stats.norm.ppf(power)
    return (za + zb) * sd / math.sqrt(n)


def deflated_sharpe_ratio(sr, sr_std, n_trials, T, skew=0.0, kurt=3.0):
    """Bailey & Lopez de Prado Deflated Sharpe Ratio: probability the observed
    Sharpe `sr` exceeds what the BEST of `n_trials` independent strategies would
    produce under a zero-skill null. sr/sr_std are per-period; T = #periods.
    Decreases as n_trials grows (more search -> more deflation)."""
    if n_trials < 1 or T < 2:
        return float("nan")
    gamma = 0.5772156649  # Euler-Mascheroni
    # expected max Sharpe under the null of n_trials draws (variance sr_std^2)
    n = max(2, n_trials)
    e_max = sr_std * ((1 - gamma) * stats.norm.ppf(1 - 1.0 / n)
                      + gamma * stats.norm.ppf(1 - 1.0 / (n * math.e)))
    denom = math.sqrt(max(1e-12, 1 - skew * sr + ((kurt - 1) / 4.0) * sr * sr))
    z = (sr - e_max) * math.sqrt(T - 1) / denom
    return float(stats.norm.cdf(z))


def pbo(returns_matrix, n_splits=10):
    """Probability of Backtest Overfitting via Combinatorially-Symmetric
    Cross-Validation (Lopez de Prado). `returns_matrix` is T x N (periods x
    strategy configurations, e.g. the threshold sweep). Split T into n_splits
    blocks; over every half/half train/test partition, pick the in-sample best
    config and measure its out-of-sample rank. PBO = fraction of partitions
    where the IS-best config lands below the OOS median (logit < 0)."""
    M = np.asarray(returns_matrix, dtype=float)
    T, N = M.shape
    if N < 2 or n_splits < 2 or n_splits % 2 != 0:
        raise ValueError("need N>=2 strategies and an even n_splits")
    blocks = np.array_split(np.arange(T), n_splits)
    half = n_splits // 2
    logits = []
    for train_idx in combinations(range(n_splits), half):
        train_b = set(train_idx)
        tr = np.concatenate([blocks[i] for i in range(n_splits) if i in train_b])
        te = np.concatenate([blocks[i] for i in range(n_splits) if i not in train_b])
        is_perf = M[tr].mean(axis=0)
        oos_perf = M[te].mean(axis=0)
        best = int(np.argmax(is_perf))
        # OOS rank of the IS-best config, as a relative rank in (0,1)
        rank = stats.rankdata(oos_perf)[best] / (N + 1)
        rank = min(max(rank, 1e-6), 1 - 1e-6)
        logits.append(math.log(rank / (1 - rank)))
    logits = np.asarray(logits)
    return float(np.mean(logits <= 0))
