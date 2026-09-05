"""Bounded incumbent traces, enabled only for excluded diagnostic runs."""

import json


class IncumbentTrace:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.points: list[dict] = []
        self.best: tuple[int, int] | None = None

    def observe(self, elapsed: float, fitness: tuple[int, int]) -> None:
        if not self.enabled or (self.best is not None and fitness >= self.best):
            return
        self.best = fitness
        self.points.append({
            "elapsed_seconds": elapsed,
            "hard_violations": fitness[0],
            "raw_penalty": fitness[1] if fitness[0] == 0 else None,
        })
        # Deterministic thinning keeps the first point and latest endpoint. No RNG
        # calls or unbounded per-generation/per-chromosome storage are introduced.
        if len(self.points) > 512:
            self.points = self.points[:-1:2] + self.points[-1:]

    def metrics(self) -> tuple:
        if not self.enabled:
            return ()
        return (("convergence_trace_json", json.dumps(
            self.points, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )),)
