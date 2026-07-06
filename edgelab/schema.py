"""Row contract for the latency-harness collector: source names, the ordered
column list, and the pyarrow schema every Parquet file conforms to."""
import pyarrow as pa

SOURCES = frozenset({
    "binance_trade", "binance_bookticker",
    "coinbase_match", "coinbase_ticker",
    "pm_oracle", "pm_clob_book", "pm_clob_price_change",
    "probe_clob", "probe_rpc", "probe_tls", "gap", "session",
})

# Ordered: envelope first, then parsed convenience columns.
COLUMNS = [
    "region_id", "source", "symbol", "window_slug",
    "recv_wall_ns", "recv_monotonic_ns", "clock_offset_ns", "clock_err_ns",
    "local_ingest_seq", "exch_seq", "payload_json",
    "price", "size", "side",
    "best_bid", "best_ask", "best_bid_sz", "best_ask_sz", "rtt_ns",
]

_TYPES = {
    "region_id": pa.string(), "source": pa.string(), "symbol": pa.string(),
    "window_slug": pa.string(),
    "recv_wall_ns": pa.int64(), "recv_monotonic_ns": pa.int64(),
    "clock_offset_ns": pa.int64(), "clock_err_ns": pa.int64(),
    "local_ingest_seq": pa.int64(), "exch_seq": pa.int64(),
    "payload_json": pa.string(),
    "price": pa.float64(), "size": pa.float64(), "side": pa.string(),
    "best_bid": pa.float64(), "best_ask": pa.float64(),
    "best_bid_sz": pa.float64(), "best_ask_sz": pa.float64(),
    "rtt_ns": pa.int64(),
}

ARROW_SCHEMA = pa.schema([(c, _TYPES[c]) for c in COLUMNS])


def empty_row() -> dict:
    """A row dict with every column present and None — callers fill what they have."""
    return {c: None for c in COLUMNS}
