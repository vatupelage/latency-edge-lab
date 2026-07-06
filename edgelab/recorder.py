"""WindowRecorder — converts the verified Polymarket `market` WS message stream
for ONE window (Up + Down tokens) into a list of top-of-book rows carrying
per-token CKS OFI. Pure/in-memory: no network, no disk — so it is unit-tested
and reused unchanged by both the live WS loop and replay mode.

Each row (one per top-of-book change, per token):
  ts, slug, horizon, side(up|down), event_type, best_bid, best_bid_sz,
  best_ask, best_ask_sz, mid, spread, ofi_inc, ofi_cum
"""

from edgelab.bookstate import BookState
from edgelab.ofi import OFIAccumulator


class WindowRecorder:
    def __init__(self, slug, horizon, up_token, down_token, open_ts, close_ts):
        self.slug = slug
        self.horizon = horizon
        self.open_ts = open_ts
        self.close_ts = close_ts
        self._side = {up_token: "up", down_token: "down"}
        self.books = {up_token: BookState(), down_token: BookState()}
        self.ofi = {up_token: OFIAccumulator(), down_token: OFIAccumulator()}
        self._last_top = {up_token: None, down_token: None}  # last emitted (bb,ba,bbq,baq)
        self.rows: list[dict] = []

    def on_ws_message(self, msg, recv_ts: float) -> None:
        et = msg.get("event_type")
        if et == "book":
            tok = msg.get("asset_id")
            if tok in self.books:
                self.books[tok].apply_snapshot(msg.get("bids"), msg.get("asks"))
                self._maybe_emit(tok, recv_ts, et)
        elif et == "price_change":
            touched = set()
            for ch in msg.get("price_changes") or []:
                tok = ch.get("asset_id")
                if tok not in self.books:
                    continue
                try:
                    self.books[tok].apply_change(ch.get("side"), float(ch["price"]),
                                                 float(ch["size"]))
                except (KeyError, TypeError, ValueError):
                    continue
                touched.add(tok)
            for tok in touched:
                self._maybe_emit(tok, recv_ts, et)

    def _maybe_emit(self, tok, recv_ts, event_type) -> None:
        book = self.books[tok]
        bb, bbq = book.best_bid()
        ba, baq = book.best_ask()
        top = (bb, ba, bbq, baq)
        if top == self._last_top[tok]:
            return                      # top-of-book unchanged -> no row
        self._last_top[tok] = top
        inc = self.ofi[tok].update((bb, bbq, ba, baq))
        mid = (bb + ba) / 2.0 if (bb is not None and ba is not None) else None
        spread = (ba - bb) if (bb is not None and ba is not None) else None
        self.rows.append({
            "ts": recv_ts,
            "slug": self.slug,
            "horizon": self.horizon,
            "side": self._side[tok],
            "event_type": event_type,
            "best_bid": bb, "best_bid_sz": bbq,
            "best_ask": ba, "best_ask_sz": baq,
            "mid": mid, "spread": spread,
            "ofi_inc": inc, "ofi_cum": self.ofi[tok].total,
        })

    def finalize(self, *, strike=None, terminal=None, up_won=None,
                 feed="binance_proxy") -> dict:
        """Per-window metadata, written alongside the rows at window close."""
        return {
            "slug": self.slug,
            "horizon": self.horizon,
            "open_ts": self.open_ts,
            "close_ts": self.close_ts,
            "strike": strike,
            "terminal": terminal,
            "up_won": up_won,
            "feed": feed,            # label the resolving-feed PROXY honestly
            "n_rows": len(self.rows),
        }
