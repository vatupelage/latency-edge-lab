"""Both-side L2 order-book reconstruction for one CLOB token.

Driven by the Polymarket `market` WebSocket protocol (verified — see
ACCESS_NOTES.md):
  - `book`         -> full snapshot (replace both sides)
  - `price_change` -> per-level delta {price, side, size}; side BUY=bid,
                      SELL=ask; size 0 removes the level.

Book sides are kept as {price: size} dicts. This is the single source of truth
the OFI calculation reads best bid/ask price+size from, so it must mirror the
exchange exactly — hence it is unit-tested against hand-checked sequences.
"""

_BID_SIDES = ("buy", "bid")
_ASK_SIDES = ("sell", "ask")


class BookState:
    def __init__(self):
        self.bids: dict[float, float] = {}   # price -> size
        self.asks: dict[float, float] = {}

    def apply_snapshot(self, bids, asks) -> None:
        """Replace both sides from a `book` event. Levels are {price,size}
        dicts (strings or floats). Zero/empty sizes are dropped."""
        self.bids = self._load(bids)
        self.asks = self._load(asks)

    @staticmethod
    def _load(levels) -> dict[float, float]:
        out: dict[float, float] = {}
        for lv in levels or []:
            try:
                p = float(lv["price"]); s = float(lv["size"])
            except (KeyError, TypeError, ValueError):
                continue
            if s > 0:
                out[p] = s
        return out

    def apply_change(self, side: str, price: float, size: float) -> None:
        """Apply one `price_change` delta. size <= 0 removes the level."""
        s = str(side).lower()
        if s in _BID_SIDES:
            store = self.bids
        elif s in _ASK_SIDES:
            store = self.asks
        else:
            return
        p = float(price); sz = float(size)
        if sz <= 0:
            store.pop(p, None)
        else:
            store[p] = sz

    def best_bid(self) -> tuple[float | None, float]:
        """(price, size) of the highest-priced bid with size>0, else (None,0)."""
        if not self.bids:
            return (None, 0.0)
        p = max(self.bids)
        return (p, self.bids[p])

    def best_ask(self) -> tuple[float | None, float]:
        """(price, size) of the lowest-priced ask with size>0, else (None,0)."""
        if not self.asks:
            return (None, 0.0)
        p = min(self.asks)
        return (p, self.asks[p])

    def mid(self) -> float | None:
        bp, _ = self.best_bid()
        ap, _ = self.best_ask()
        if bp is None or ap is None:
            return None
        return (bp + ap) / 2.0

    def spread(self) -> float | None:
        bp, _ = self.best_bid()
        ap, _ = self.best_ask()
        if bp is None or ap is None:
            return None
        return ap - bp
