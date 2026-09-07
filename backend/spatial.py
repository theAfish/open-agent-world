"""Pure rectangle placement, independent of cards, persistence, and rendering."""
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Rectangle:
    x: float
    y: float
    width: float
    height: float

    def overlaps(self, other: "Rectangle", *, gap: float = 0) -> bool:
        return not (self.x + self.width + gap <= other.x or other.x + other.width + gap <= self.x
                    or self.y + self.height + gap <= other.y or other.y + other.height + gap <= self.y)


def find_free_region(preferred: Rectangle, obstacles: Iterable[Rectangle], *, gap: float = 80) -> Rectangle:
    """Choose the closest clear boundary candidate; this is not a global packing optimizer.

    Coordinates, dimensions and gap use the caller's units; dimensions must be
    positive and gap nonnegative. With finite obstacles on an unbounded plane,
    the outermost boundary always supplies a free candidate. Nothing is moved.
    """
    obstacles = tuple(obstacles)
    candidates = {(preferred.x, preferred.y)}
    for obstacle in obstacles:
        candidates.update({
            (obstacle.x + obstacle.width + gap, preferred.y),
            (obstacle.x - preferred.width - gap, preferred.y),
            (preferred.x, obstacle.y + obstacle.height + gap),
            (preferred.x, obstacle.y - preferred.height - gap),
        })
    ordered = sorted(candidates, key=lambda p: ((p[0] - preferred.x) ** 2 + (p[1] - preferred.y) ** 2, p))
    for x, y in ordered:
        region = Rectangle(x, y, preferred.width, preferred.height)
        if not any(region.overlaps(obstacle, gap=gap) for obstacle in obstacles):
            return region
    raise ValueError("No clear region candidate")
