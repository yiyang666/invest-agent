"""Deterministic RSI calculations for validated NAV series."""

from __future__ import annotations

from collections.abc import Sequence


def wilder_rsi(values: Sequence[float], period: int = 14) -> list[float | None]:
    """Return Wilder's RSI on a 0-100 scale without forward filling gaps.

    The first value is available after ``period`` observed changes.  A flat
    window is neutral (50); a window with gains and no losses is 100.
    """
    if period < 1:
        raise ValueError("period must be positive")
    if any(value <= 0 for value in values):
        raise ValueError("NAV values must be positive")

    result: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return result

    changes = [current - previous for previous, current in zip(values, values[1:])]
    average_gain = sum(max(change, 0.0) for change in changes[:period]) / period
    average_loss = sum(max(-change, 0.0) for change in changes[:period]) / period

    def value(gain: float, loss: float) -> float:
        if gain == 0 and loss == 0:
            return 50.0
        if loss == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    result[period] = value(average_gain, average_loss)
    for index, change in enumerate(changes[period:], start=period + 1):
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        average_gain = (average_gain * (period - 1) + gain) / period
        average_loss = (average_loss * (period - 1) + loss) / period
        result[index] = value(average_gain, average_loss)
    return result
