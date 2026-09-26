"""Persistent print finishes and the product's pack-opening probability table."""
from __future__ import annotations

from collections.abc import Mapping
from math import fsum, isfinite
import random
from typing import Literal, TypeVar


CardFinish = Literal["normal", "foil", "rainbow", "starlight", "laser"]

# Relative weights; this is the single location for balancing new pack rewards.
# Changing it never changes finishes already stored in a collection or world.
CARD_FINISH_WEIGHTS: dict[CardFinish, float] = {
    "normal": 0.72,
    "foil": 0.16,
    "rainbow": 0.06,
    "starlight": 0.04,
    "laser": 0.02,
}

T = TypeVar("T")


def weighted_choice(weights: Mapping[T, float], random_value: float) -> T:
    """Select from ordered relative weights using one sample in [0, 1).

    Injecting the sample keeps deterministic boundaries and seeded callers
    testable. Zero weights disable an outcome without special-case roll logic.
    """
    if not isfinite(random_value) or not 0 <= random_value < 1:
        raise ValueError("random_value must be finite and in [0, 1)")
    if not weights or any(not isfinite(weight) or weight < 0 for weight in weights.values()):
        raise ValueError("weights must be finite, nonnegative, and nonempty")
    total = fsum(weights.values())
    if not isfinite(total) or total <= 0:
        raise ValueError("weights must have a positive finite total")
    threshold = random_value * total
    positive = [(value, weight) for value, weight in weights.items() if weight > 0]
    cumulative: list[float] = []
    for value, weight in positive:
        cumulative.append(weight)
        if threshold < fsum(cumulative):
            return value
    # Guard against the multiplication rounding up to total for the last sample.
    return positive[-1][0]


def roll_card_finish() -> CardFinish:
    return weighted_choice(CARD_FINISH_WEIGHTS, random.random())
