"""Domain models for a discrete, deterministic *software* robot simulator.

This is deliberately not a physics engine, kinematics stack, or ROS integration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

Cell = tuple[int, int]


class Status(str, Enum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


@dataclass(frozen=True)
class Mission:
    mission_id: str
    object_id: str
    pickup: Cell
    dropoff: Cell


@dataclass
class Robot:
    location: Cell
    battery: int = 100
    carrying: str | None = None
    steps: int = 0


@dataclass
class World:
    width: int
    height: int
    blocked: set[Cell]
    objects: dict[str, Cell]
    delivered: dict[str, Cell] = field(default_factory=dict)
    obstacle_events: dict[int, list[Cell]] = field(default_factory=dict)

    def open(self, cell: Cell) -> bool:
        x, y = cell
        return 0 <= x < self.width and 0 <= y < self.height and cell not in self.blocked

    def apply_events(self, tick: int) -> list[Cell]:
        added = self.obstacle_events.get(tick, [])
        self.blocked.update(added)
        return added


@dataclass
class Recorder:
    events: list[dict[str, Any]] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=lambda: {
        "robot_steps": 0, "replans": 0, "retries": 0, "faults_injected": 0,
        "timeouts": 0, "safety_stops": 0,
    })

    def emit(self, tick: int, mission: str, event: str, **fields: Any) -> None:
        self.events.append({"tick": tick, "mission": mission, "event": event, **fields})

    def increment(self, counter: str) -> None:
        self.counters[counter] = self.counters.get(counter, 0) + 1


@dataclass
class Context:
    world: World
    robot: Robot
    mission: Mission
    recorder: Recorder
    fault_plan: dict[str, set[int]]
    attempts: dict[str, int]
    tick: int = 0
    last_error: str = ""

    def emit(self, event: str, **data: Any) -> None:
        self.recorder.emit(self.tick, self.mission.mission_id, event, **data)

    def attempt(self, action: str) -> bool:
        """True if this invocation has a deterministic injected failure."""
        key = f"{self.mission.mission_id}:{action}"
        n = self.attempts.get(key, 0) + 1
        self.attempts[key] = n
        if n in self.fault_plan.get(key, set()):
            self.recorder.increment("faults_injected")
            self.last_error = f"injected failure: {action}, attempt {n}"
            self.emit("FAULT_INJECTED", action=action, attempt=n)
            return True
        return False
