"""Per-event clock envelope. `parse_chrony_tracking` is pure (text -> ns);
ClockStamper caches a chrony reading and stamps each event with wall+monotonic
time, the offset/error bound, and a per-source ingest sequence."""
import re
import subprocess
import time

from edgelab.schema import empty_row, COLUMNS

_COLSET = set(COLUMNS)


def parse_chrony_tracking(text: str) -> tuple[int, int]:
    """`chronyc tracking` text -> (offset_ns signed, err_ns).
    err = root_dispersion + root_delay/2  (NTP maximum-error bound)."""
    def grab(label: str) -> float:
        m = re.search(rf"{label}\s*:\s*([-+]?[0-9.]+)", text)
        if not m:
            raise ValueError(f"chrony field not found: {label}")
        return float(m.group(1))
    offset = grab("Last offset")
    dispersion = grab("Root dispersion")
    delay = grab("Root delay")
    offset_ns = round(offset * 1e9)
    err_ns = round((dispersion + delay / 2.0) * 1e9)
    return offset_ns, err_ns


def read_chrony() -> tuple[int, int]:
    """Live reading via `chronyc tracking`; returns (0, very-large) if chrony is
    unavailable so the error bound is honestly huge rather than silently small."""
    try:
        out = subprocess.run(["chronyc", "tracking"], capture_output=True,
                             text=True, timeout=2).stdout
        return parse_chrony_tracking(out)
    except Exception:
        return 0, 10**12  # 1 second: unknown clock => refuse to claim precision


class ClockStamper:
    def __init__(self, region_id: str, reader=read_chrony, refresh_s: float = 5.0):
        self.region_id = region_id
        self._reader = reader
        self._refresh_s = refresh_s
        self._cache = reader()
        self._cache_mono = time.monotonic()
        self._seq: dict[str, int] = {}

    def _clock(self) -> tuple[int, int]:
        if time.monotonic() - self._cache_mono >= self._refresh_s:
            self._cache = self._reader()
            self._cache_mono = time.monotonic()
        return self._cache

    def stamp(self, source: str, **parsed) -> dict:
        bad = set(parsed) - _COLSET
        if bad:
            raise KeyError(f"unknown columns: {sorted(bad)}")
        offset_ns, err_ns = self._clock()
        seq = self._seq.get(source, 0)
        self._seq[source] = seq + 1
        row = empty_row()
        row.update(parsed)
        row["region_id"] = self.region_id
        row["source"] = source
        row["recv_wall_ns"] = time.time_ns()
        row["recv_monotonic_ns"] = time.monotonic_ns()
        row["clock_offset_ns"] = offset_ns
        row["clock_err_ns"] = err_ns
        row["local_ingest_seq"] = seq
        return row
