"""In-process metrics with a Prometheus text exporter. Deliberately tiny: a
counter/histogram-ish summary per (name, labels) without external deps."""

from __future__ import annotations

import threading
from collections import defaultdict


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self.sums: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[float, int]] = {}

    @staticmethod
    def _k(name: str, labels: dict[str, str]) -> tuple[str, tuple[tuple[str, str], ...]]:
        return name, tuple(sorted(labels.items()))

    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        with self._lock:
            self.counters[self._k(name, labels)] += value

    def observe(self, name: str, value: float, **labels: str) -> None:
        k = self._k(name, labels)
        with self._lock:
            s, n = self.sums.get(k, (0.0, 0))
            self.sums[k] = (s + value, n + 1)

    def render(self) -> str:
        def fmt(labels: tuple[tuple[str, str], ...]) -> str:
            if not labels:
                return ""
            inner = ",".join(f'{k}="{v.replace(chr(34), "")}"' for k, v in labels)
            return "{" + inner + "}"

        lines = []
        with self._lock:
            for (name, labels), v in sorted(self.counters.items()):
                lines.append(f"radar_{name}_total{fmt(labels)} {v}")
            for (name, labels), (s, n) in sorted(self.sums.items()):
                lines.append(f"radar_{name}_sum{fmt(labels)} {s}")
                lines.append(f"radar_{name}_count{fmt(labels)} {n}")
        return "\n".join(lines) + "\n"


METRICS = Metrics()
