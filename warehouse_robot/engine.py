"""Mission orchestration, deterministic tick scheduling, failure and metrics tracking."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import json
import statistics

from .model import Context, Recorder, Status
from .lifecycle import MissionLifecycle, Phase
from .scenario import Scenario
from .skills import Navigate, Grasp, Place, Verify
from .tree import Condition, Sequence, Retry, Timeout


@dataclass
class MissionResult:
    mission: str
    status: str
    start_tick: int
    end_tick: int
    duration_ticks: int
    reason: str | None = None


def build_behavior_tree(scenario: Scenario):
    budget = scenario.retry_budget
    timeout = scenario.nav_timeout_ticks
    return Sequence(
        Condition("safe starting battery", lambda c: c.robot.battery >= scenario.min_start_battery),
        Retry("navigate_pickup", Timeout("navigate_pickup", Navigate("navigate_pickup", "pickup"), timeout), budget),
        Retry("grasp", Grasp(), budget),
        Retry("navigate_dropoff", Timeout("navigate_dropoff", Navigate("navigate_dropoff", "dropoff"), timeout), budget),
        Retry("place", Place(), budget),
        Verify(),
    )


class RobotMissionEngine:
    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self.recorder = Recorder()
        self.results: list[MissionResult] = []
        self.attempts: dict[str, int] = {}
        self.tick = 0

    def run(self) -> dict:
        s = self.scenario
        for mission in s.missions:
            start = self.tick + 1
            if self.tick >= s.max_ticks:
                self.results.append(MissionResult(mission.mission_id, "FAILURE", start, self.tick, 0,
                                                  "scenario tick budget exhausted"))
                continue
            ctx = Context(s.world, s.robot, mission, self.recorder, s.faults, self.attempts)
            tree = build_behavior_tree(s)
            lifecycle = MissionLifecycle()
            ctx.tick = self.tick
            lifecycle.transition(Phase.EXECUTING, ctx, "mission dispatched")
            ctx.emit("MISSION_START", object_id=mission.object_id,
                     pickup=list(mission.pickup), dropoff=list(mission.dropoff))
            final = Status.RUNNING
            while self.tick < s.max_ticks:
                self.tick += 1
                ctx.tick = self.tick
                additions = s.world.apply_events(self.tick)
                if additions:
                    ctx.emit("OBSTACLE_UPDATE", new_blocks=[list(c) for c in additions])
                if lifecycle.phase is Phase.RECOVERING:
                    lifecycle.transition(Phase.EXECUTING, ctx, "retry executing")
                before_retries = self.recorder.counters["retries"]
                final = tree.tick(ctx)
                if final is Status.RUNNING and self.recorder.counters["retries"] > before_retries:
                    lifecycle.transition(Phase.RECOVERING, ctx, "skill retry scheduled")
                if final is not Status.RUNNING:
                    break
            if final is Status.RUNNING:
                ctx.last_error = f"mission exceeded global {s.max_ticks}-tick budget"
                ctx.emit("MISSION_TIMEOUT", reason=ctx.last_error)
            succeeded = final is Status.SUCCESS
            lifecycle.transition(Phase.SUCCEEDED if succeeded else Phase.FAILED,
                                 ctx, "completion verified" if succeeded else ctx.last_error)
            status = "SUCCESS" if succeeded else "FAILURE"
            self.results.append(MissionResult(mission.mission_id, status, start, self.tick,
                                              max(0, self.tick - start + 1),
                                              None if succeeded else ctx.last_error or "skill failed"))
            ctx.emit("MISSION_END", status=status, reason=self.results[-1].reason)
        succeeded = sum(r.status == "SUCCESS" for r in self.results)
        durations = [r.duration_ticks for r in self.results]
        total = len(self.results)
        return {
            "scenario": s.name,
            "result": "PASS" if succeeded == total else "FAIL",
            "successes": succeeded,
            "failures": total - succeeded,
            "completion_rate": round(succeeded / total, 4),
            "total_ticks": self.tick,
            "mean_mission_ticks": round(statistics.mean(durations), 2),
            "p95_mission_ticks": sorted(durations)[max(0, (95 * len(durations) + 99) // 100 - 1)],
            "counters": dict(self.recorder.counters),
            "missions": [asdict(m) for m in self.results],
            "final_robot_state": {
                "location": list(s.robot.location),
                "battery": s.robot.battery,
                "carrying": s.robot.carrying,
            },
        }

    def export(self, folder: str | Path, summary: dict) -> None:
        path = Path(folder)
        path.mkdir(parents=True, exist_ok=True)
        (path / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        with (path / "events.jsonl").open("w", encoding="utf-8") as stream:
            for event in self.recorder.events:
                stream.write(json.dumps(event, sort_keys=True) + "\n")
        lines = ["mission,status,start_tick,end_tick,duration_ticks,reason"]
        import csv
        with (path / "missions.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(lines[0].split(","))
            for m in self.results:
                writer.writerow([m.mission, m.status, m.start_tick, m.end_tick,
                                 m.duration_ticks, m.reason or ""])
