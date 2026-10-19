"""Deterministic outcome labels. No LLM confidence is used for scoring."""
import math
import statistics

FLAT_DELTA = 0.30


def threshold(deltas: list[float]) -> float | None:
    finite = [float(v) for v in deltas if v is not None and math.isfinite(float(v))]
    if len(finite) < 60:
        return None
    return max(FLAT_DELTA, 2 * statistics.stdev(finite[-60:]))


def actual_label(delta: float, extreme_threshold: float | None) -> str | None:
    if not math.isfinite(delta):
        return None
    if abs(delta) <= FLAT_DELTA + 1e-9:
        return "flat"
    if extreme_threshold is None:
        return None  # Do not force a five-class label without the frozen volatility threshold.
    if abs(delta) >= extreme_threshold:
        return "extreme_up" if delta > 0 else "extreme_down"
    return "up" if delta > 0 else "down"
