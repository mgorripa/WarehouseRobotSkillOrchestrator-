"""Deterministic 4-neighbor A* route planning on a *logical* warehouse grid."""
from __future__ import annotations

from heapq import heappop, heappush
from .model import Cell, World


def route(world: World, start: Cell, target: Cell) -> list[Cell] | None:
    """Return the cells after start, up to target; None means no route exists."""
    if not world.open(target):
        return None
    if start == target:
        return []

    def h(p: Cell) -> int:
        return abs(p[0] - target[0]) + abs(p[1] - target[1])

    frontier: list[tuple[int, int, Cell]] = [(h(start), 0, start)]
    predecessor: dict[Cell, Cell | None] = {start: None}
    best: dict[Cell, int] = {start: 0}
    while frontier:
        _, cost, current = heappop(frontier)
        if cost != best[current]:
            continue
        if current == target:
            path: list[Cell] = []
            cursor = target
            while cursor != start:
                path.append(cursor)
                parent = predecessor[cursor]
                assert parent is not None
                cursor = parent
            return list(reversed(path))
        x, y = current
        for other in ((x + 1, y), (x, y + 1), (x - 1, y), (x, y - 1)):
            if not world.open(other):
                continue
            new_cost = cost + 1
            if new_cost < best.get(other, 10**9):
                best[other] = new_cost
                predecessor[other] = current
                heappush(frontier, (new_cost + h(other), new_cost, other))
    return None
