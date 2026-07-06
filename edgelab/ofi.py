"""Cont-Kukanov-Stoikov level-1 Order-Flow Imbalance (OFI).

OFI summarises, between two consecutive best-quote states, the net pressure
from limit-order arrivals/cancellations and trades at the top of book. Positive
OFI = net buying pressure (price should rise). It is computed purely from the
best bid/ask price+size of the binary's OWN book, so it is independent of BTC —
that is the whole point of Edge B.

Definition (state = (bid_price, bid_size, ask_price, ask_size)):
  bid:  P_b' > P_b -> e_b = Q_b'        ;  P_b' = P_b -> e_b = Q_b' - Q_b
        P_b' < P_b -> e_b = -Q_b
  ask:  P_a' > P_a -> e_a = -Q_a        ;  P_a' = P_a -> e_a = Q_a' - Q_a
        P_a' < P_a -> e_a = Q_a'
  OFI = e_b - e_a

A side whose price is None (empty book side) contributes 0 to that side's term.
"""


def _bid_flow(prev_p, prev_q, cur_p, cur_q) -> float:
    if cur_p is None or prev_p is None:
        return 0.0
    if cur_p > prev_p:
        return cur_q
    if cur_p == prev_p:
        return cur_q - prev_q
    return -prev_q


def _ask_flow(prev_p, prev_q, cur_p, cur_q) -> float:
    if cur_p is None or prev_p is None:
        return 0.0
    if cur_p > prev_p:
        return -prev_q
    if cur_p == prev_p:
        return cur_q - prev_q
    return cur_q


def ofi_increment(prev, cur) -> float:
    """OFI contribution of moving from state `prev` to `cur`. `prev` None
    (no predecessor) -> 0.0. Each state is (bid_p, bid_q, ask_p, ask_q)."""
    if prev is None or cur is None:
        return 0.0
    pbp, pbq, pap, paq = prev
    cbp, cbq, cap, caq = cur
    e_b = _bid_flow(pbp, pbq, cbp, cbq)
    e_a = _ask_flow(pap, paq, cap, caq)
    return e_b - e_a


class OFIAccumulator:
    """Running sum of OFI increments across a sequence of best-quote states
    (e.g. one trade window). `update` returns the increment just applied."""

    def __init__(self):
        self.prev = None
        self.total = 0.0

    def update(self, state) -> float:
        inc = ofi_increment(self.prev, state)
        self.total += inc
        self.prev = state
        return inc
