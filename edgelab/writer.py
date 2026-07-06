"""Restart-safe, immutable, time-bucketed Parquet writer. One file per
(source, minute) buffer, flushed on minute-change / N-rows / shutdown; a closed
file is never overwritten (suffixes -1, -2, ...). Mirrors edgelab.logger's
'one immutable file per closed unit' guarantee."""
import os
import time
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq

from edgelab.schema import ARROW_SCHEMA, COLUMNS


class RotatingParquetWriter:
    def __init__(self, out_dir: str, rotate_secs: int = 60, rotate_n: int = 5000,
                 clock=time.time, region_id=None):
        self.out_dir = out_dir
        self.rotate_n = rotate_n
        self._clock = clock
        self._region_id = region_id
        # key: source -> {"minute": int, "rows": list[dict]}
        self._buffers: dict[str, dict] = {}

    @staticmethod
    def _minute(row: dict, fallback_clock) -> int:
        wall_ns = row.get("recv_wall_ns")
        secs = (wall_ns / 1e9) if wall_ns else fallback_clock()
        return int(secs // 60)

    def write(self, row: dict) -> None:
        source = row["source"]
        minute = self._minute(row, self._clock)
        buf = self._buffers.get(source)
        if buf is not None and buf["minute"] != minute:
            self._flush(source)
            buf = None
        if buf is None:
            buf = {"minute": minute, "rows": []}
            self._buffers[source] = buf
        buf["rows"].append({c: row.get(c) for c in COLUMNS})
        if len(buf["rows"]) >= self.rotate_n:
            self._flush(source)

    def _flush(self, source: str) -> None:
        buf = self._buffers.pop(source, None)
        if not buf or not buf["rows"]:
            return
        epoch_minute = buf["minute"]
        day = datetime.fromtimestamp(epoch_minute * 60, tz=timezone.utc).strftime("%Y-%m-%d")
        part = os.path.join(self.out_dir, "events", f"day={day}", f"source={source}")
        os.makedirs(part, exist_ok=True)
        base = f"{self._region_id}-{epoch_minute}" if self._region_id else f"{epoch_minute}"
        path = os.path.join(part, f"{base}.parquet")
        n = 1
        while os.path.exists(path):
            path = os.path.join(part, f"{base}-{n}.parquet")
            n += 1
        tbl = pa.Table.from_pylist(buf["rows"], schema=ARROW_SCHEMA)
        pq.write_table(tbl, path, compression="zstd")

    def flush_all(self) -> None:
        for source in list(self._buffers):
            self._flush(source)
