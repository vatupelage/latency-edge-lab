"""Per-source exchange-sequence hole detection. Only forward holes count as
dropped messages; duplicates / out-of-order / missing seq are ignored."""


class SeqGapTracker:
    def __init__(self):
        self._last: dict[str, int] = {}

    def check(self, source: str, seq):
        if seq is None:
            return None
        last = self._last.get(source)
        if last is None:
            self._last[source] = seq
            return None
        if seq <= last:
            return None
        if seq > last + 1:
            gap = {"source": source, "gap_start": last + 1,
                   "gap_end": seq - 1, "count": seq - 1 - last}
            self._last[source] = seq
            return gap
        self._last[source] = seq
        return None
