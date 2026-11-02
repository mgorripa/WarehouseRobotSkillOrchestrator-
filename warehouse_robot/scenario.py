"""Strict schema validation and replayable JSON scenario loading."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json

from .model import Cell, Mission, Robot, World


@dataclass
class Scenario:
    name: str
    robot: Robot
    world: World
    missions: list[Mission]
    faults: dict[str, set[int]]
    max_ticks: int = 250
    min_start_battery: int = 10
    nav_timeout_ticks: int = 35
    retry_budget: int = 3
    expected_successes: int | None = None


def _cell(raw: object, label: str) -> Cell:
    if not (isinstance(raw, list) and len(raw) == 2 and all(type(x) is int for x in raw)):
        raise ValueError(f"{label} must be [integer_x, integer_y]")
    return (raw[0], raw[1])


def load(path: str | Path) -> Scenario:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    width, height = data["width"], data["height"]
    if type(width) is not int or type(height) is not int or width < 2 or height < 2:
        raise ValueError("warehouse must have width/height >= 2")
    blocked = {_cell(c, "obstacle") for c in data.get("obstacles", [])}
    world = World(width=width, height=height, blocked=blocked, objects={})
    for cell in blocked:
        x, y = cell
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError(f"obstacle outside grid: {cell}")
    robot_data = data["robot"]
    robot = Robot(_cell(robot_data["location"], "robot location"), robot_data.get("battery", 100))
    if not world.open(robot.location):
        raise ValueError("robot must begin on a traversable cell")
    if type(robot.battery) is not int or not 0 <= robot.battery <= 100:
        raise ValueError("battery must be between 0 and 100")
    for obj in data["objects"]:
        pos = _cell(obj["location"], f"object {obj['id']}")
        if not world.open(pos):
            raise ValueError(f"object {obj['id']} must be on traversable cell")
        if obj["id"] in world.objects:
            raise ValueError(f"duplicate object: {obj['id']}")
        world.objects[obj["id"]] = pos
    missions = []
    ids = set()
    for item in data["missions"]:
        mission = Mission(item["id"], item["object_id"],
                          _cell(item["pickup"], "pickup"), _cell(item["dropoff"], "dropoff"))
        if mission.mission_id in ids:
            raise ValueError(f"duplicate mission id {mission.mission_id}")
        ids.add(mission.mission_id)
        if mission.object_id not in world.objects:
            raise ValueError(f"mission object is unknown: {mission.object_id}")
        if world.objects[mission.object_id] != mission.pickup:
            raise ValueError(f"pickup mismatch for {mission.object_id}")
        if not world.open(mission.dropoff):
            raise ValueError(f"invalid dropoff for {mission.object_id}")
        missions.append(mission)
    if len({m.object_id for m in missions}) != len(missions):
        raise ValueError("each object may appear in only one mission")
    if not missions:
        raise ValueError("scenario needs at least one mission")
    events: dict[int, list[Cell]] = {}
    for tick_text, cells in data.get("obstacle_events", {}).items():
        tick = int(tick_text)
        if tick < 1:
            raise ValueError("obstacle event ticks must be positive")
        events[tick] = [_cell(c, "obstacle event") for c in cells]
        for cell in events[tick]:
            x, y = cell
            if not (0 <= x < width and 0 <= y < height):
                raise ValueError(f"dynamic obstacle outside grid: {cell}")
    world.obstacle_events = events
    faults: dict[str, set[int]] = {}
    for key, attempts in data.get("faults", {}).items():
        if not isinstance(attempts, list) or not all(type(i) is int and i > 0 for i in attempts):
            raise ValueError(f"invalid fault plan for {key}")
        mission_id, sep, action = key.partition(":")
        if not sep or mission_id not in ids or action not in {"navigate_pickup", "navigate_dropoff", "grasp", "place"}:
            raise ValueError(f"invalid fault target {key}")
        faults[key] = set(attempts)
    policy = data.get("policy", {})
    max_ticks = policy.get("max_ticks", 250)
    nav_timeout = policy.get("nav_timeout_ticks", 35)
    retries = policy.get("retry_budget", 3)
    min_battery = policy.get("min_start_battery", 10)
    if not all(type(x) is int and x > 0 for x in (max_ticks, nav_timeout, retries, min_battery)):
        raise ValueError("policy limits must be positive integers")
    return Scenario(name=data.get("name", Path(path).stem), world=world, robot=robot,
                    missions=missions, faults=faults, max_ticks=max_ticks,
                    nav_timeout_ticks=nav_timeout, retry_budget=retries,
                    min_start_battery=min_battery,
                    expected_successes=data.get("expected_successes"))
