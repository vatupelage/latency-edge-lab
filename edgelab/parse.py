"""Pure per-feed parsers: raw WS message dict -> list of parsed-column dicts.
Each row carries a `source`; the caller adds the clock envelope + payload_json.
Unrecognized messages return []. Sample shapes verified live 2026-06-21."""


def _f(x):
    return None if x is None else float(x)


def parse_binance(msg: dict) -> list[dict]:
    d = msg.get("data") or {}
    et = d.get("e")
    if "@bookTicker" in (msg.get("stream") or "") or ("b" in d and "a" in d and et is None):
        return [{"source": "binance_bookticker",
                 "best_bid": _f(d.get("b")), "best_ask": _f(d.get("a")),
                 "best_bid_sz": _f(d.get("B")), "best_ask_sz": _f(d.get("A")),
                 "exch_seq": d.get("u")}]
    if et == "trade":
        return [{"source": "binance_trade",
                 "price": _f(d.get("p")), "size": _f(d.get("q")),
                 # m=True => buyer is market maker => taker SOLD
                 "side": "sell" if d.get("m") else "buy",
                 "exch_seq": d.get("t")}]
    return []


def parse_coinbase(msg: dict) -> list[dict]:
    t = msg.get("type")
    if t in ("match", "last_match"):
        return [{"source": "coinbase_match",
                 "price": _f(msg.get("price")), "size": _f(msg.get("size")),
                 "side": msg.get("side"), "exch_seq": msg.get("sequence")}]
    if t == "ticker":
        return [{"source": "coinbase_ticker",
                 "price": _f(msg.get("price")),
                 "best_bid": _f(msg.get("best_bid")), "best_ask": _f(msg.get("best_ask")),
                 "best_bid_sz": _f(msg.get("best_bid_size")),
                 "best_ask_sz": _f(msg.get("best_ask_size")),
                 "exch_seq": msg.get("sequence")}]
    return []


def parse_pm_oracle(msg: dict) -> list[dict]:
    if msg.get("topic") != "crypto_prices" or msg.get("type") != "update":
        return []
    p = msg.get("payload") or {}
    val = p.get("value")
    if val is None:
        val = p.get("full_accuracy_value")
    return [{"source": "pm_oracle", "price": _f(val),
             "symbol_raw": p.get("symbol")}]


def _top(levels, want_max):
    best = None
    best_px = None
    for lv in levels or []:
        px = _f(lv.get("price"))
        sz = _f(lv.get("size"))
        if px is None or sz is None or sz <= 0:
            continue
        if best_px is None or (px > best_px if want_max else px < best_px):
            best_px, best = px, lv
    return best or {}


def parse_pm_clob(msg: dict) -> list[dict]:
    et = msg.get("event_type")
    if et == "book":
        bids = msg.get("bids") or []
        asks = msg.get("asks") or []
        tb = _top(bids, True)
        ta = _top(asks, False)
        return [{"source": "pm_clob_book", "asset_id": msg.get("asset_id"),
                 "best_bid": _f(tb.get("price")), "best_ask": _f(ta.get("price")),
                 "best_bid_sz": _f(tb.get("size")), "best_ask_sz": _f(ta.get("size"))}]
    if et == "price_change":
        out = []
        for ch in msg.get("price_changes") or []:
            out.append({"source": "pm_clob_price_change",
                        "asset_id": msg.get("asset_id"),
                        "price": _f(ch.get("price")), "size": _f(ch.get("size")),
                        "side": ch.get("side")})
        return out
    return []
