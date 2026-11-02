import json
from pathlib import Path
import tempfile
import unittest

from warehouse_robot.cli import ROOT, execute
from warehouse_robot.engine import RobotMissionEngine
from warehouse_robot.model import Context, Mission, Recorder, Robot, Status, World
from warehouse_robot.navigation import route
from warehouse_robot.scenario import load
from warehouse_robot.tree import Condition, Retry, Sequence, Selector, Timeout

SCENARIOS = ROOT / "scenarios"


class RoutingTests(unittest.TestCase):
    def test_shortest_route(self):
        w = World(5, 5, {(1, 0)}, {})
        result = route(w, (0, 0), (2, 0))
        self.assertEqual(len(result), 4)
        self.assertEqual(result[-1], (2, 0))
        self.assertNotIn((1, 0), result)

    def test_no_route(self):
        w = World(3, 3, {(1, 0), (1, 1), (1, 2)}, {})
        self.assertIsNone(route(w, (0, 0), (2, 2)))

    def test_already_at_goal(self):
        w = World(3, 3, set(), {})
        self.assertEqual(route(w, (1, 1), (1, 1)), [])

    def test_invalid_destination(self):
        w = World(3, 3, set(), {})
        self.assertIsNone(route(w, (0, 0), (10, 10)))


class TreeTests(unittest.TestCase):
    def setUp(self):
        mission = Mission("m1", "box", (0, 0), (2, 2))
        self.ctx = Context(World(4, 4, set(), {"box": (0, 0)}),
                           Robot((0, 0)), mission, Recorder(), {}, {})

    def test_sequence(self):
        tree = Sequence(Condition("true", lambda c: True), Condition("false", lambda c: False))
        self.assertIs(tree.tick(self.ctx), Status.FAILURE)

    def test_selector(self):
        tree = Selector(Condition("false", lambda c: False), Condition("true", lambda c: True))
        self.assertIs(tree.tick(self.ctx), Status.SUCCESS)

    def test_retries_exhausted(self):
        tree = Retry("fail", Condition("false", lambda c: False), 3)
        self.assertEqual([tree.tick(self.ctx) for _ in range(3)],
                         [Status.RUNNING, Status.RUNNING, Status.FAILURE])
        self.assertEqual(self.ctx.recorder.counters["retries"], 2)

    def test_timeout(self):
        class Stuck(Condition):
            def tick(self, context):
                return Status.RUNNING
        tree = Timeout("stuck", Stuck("always", lambda c: True), 2)
        self.assertIs(tree.tick(self.ctx), Status.RUNNING)
        self.assertIs(tree.tick(self.ctx), Status.FAILURE)
        self.assertEqual(self.ctx.recorder.counters["timeouts"], 1)


class IntegrationTests(unittest.TestCase):
    def test_nominal(self):
        summary = execute(SCENARIOS / "nominal.json")
        self.assertEqual(summary["successes"], 1)
        self.assertEqual(summary["counters"]["retries"], 0)

    def test_demo_injects_and_recovers(self):
        scenario = load(SCENARIOS / "demo_recovery.json")
        engine = RobotMissionEngine(scenario)
        summary = engine.run()
        self.assertEqual(summary["successes"], 2)
        self.assertEqual(summary["final_robot_state"]["carrying"], None)
        self.assertEqual(summary["counters"]["faults_injected"], 3)
        self.assertGreaterEqual(summary["counters"]["replans"], 1)
        self.assertEqual(scenario.world.delivered["box-A"], (9, 1))
        self.assertEqual(scenario.world.delivered["box-B"], (10, 5))

    def test_timeout_recovery(self):
        summary = execute(SCENARIOS / "timeout_recovery.json")
        self.assertEqual(summary["successes"], 1)
        self.assertGreater(summary["counters"]["timeouts"], 0)

    def test_failure_after_exhaustion(self):
        summary = execute(SCENARIOS / "fault_exhaustion.json")
        self.assertEqual(summary["failures"], 1)
        self.assertEqual(summary["counters"]["faults_injected"], 3)
        self.assertEqual(summary["counters"]["retries"], 2)
        self.assertIn("injected failure", summary["missions"][0]["reason"])

    def test_unreachable(self):
        summary = execute(SCENARIOS / "unreachable.json")
        self.assertEqual(summary["successes"], 0)
        self.assertIn("unreachable", summary["missions"][0]["reason"])

    def test_low_battery(self):
        summary = execute(SCENARIOS / "low_battery.json")
        self.assertEqual(summary["successes"], 0)
        self.assertIn("battery", summary["missions"][0]["reason"])

    def test_deterministic_replay(self):
        first = RobotMissionEngine(load(SCENARIOS / "demo_recovery.json"))
        second = RobotMissionEngine(load(SCENARIOS / "demo_recovery.json"))
        self.assertEqual(first.run(), second.run())
        self.assertEqual(first.recorder.events, second.recorder.events)

    def test_jsonl_export(self):
        engine = RobotMissionEngine(load(SCENARIOS / "nominal.json"))
        result = engine.run()
        with tempfile.TemporaryDirectory() as root:
            engine.export(root, result)
            self.assertTrue((Path(root) / "missions.csv").exists())
            self.assertEqual(json.loads((Path(root) / "summary.json").read_text()), result)
            events = [json.loads(x) for x in (Path(root) / "events.jsonl").read_text().splitlines()]
            self.assertIn("MISSION_END", [event["event"] for event in events])

    def test_all_scenario_expectations(self):
        for path in SCENARIOS.glob("*.json"):
            with self.subTest(path=path.name):
                s = load(path)
                self.assertEqual(execute(path)["successes"], s.expected_successes)


class ValidationTests(unittest.TestCase):
    def test_invalid_fault_target(self):
        d = json.loads((SCENARIOS / "nominal.json").read_text())
        d["faults"] = {"fake:grasp": [1]}
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "bad.json"
            path.write_text(json.dumps(d))
            with self.assertRaisesRegex(ValueError, "invalid fault target"):
                load(path)

    def test_reject_duplicate_object_ids(self):
        d = json.loads((SCENARIOS / "nominal.json").read_text())
        d["objects"].append(d["objects"][0])
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "bad.json"
            path.write_text(json.dumps(d))
            with self.assertRaisesRegex(ValueError, "duplicate object"):
                load(path)


if __name__ == "__main__":
    unittest.main()

class StressTests(unittest.TestCase):
    def test_seeded_stress_is_reproducible(self):
        from warehouse_robot.cli import main
        with tempfile.TemporaryDirectory() as folder:
            a, b = Path(folder) / "a", Path(folder) / "b"
            self.assertEqual(main(["stress", "--trials", "10", "--seed", "12", "--out", str(a)]), 0)
            self.assertEqual(main(["stress", "--trials", "10", "--seed", "12", "--out", str(b)]), 0)
            self.assertEqual((a / "stress_scorecard.json").read_bytes(), (b / "stress_scorecard.json").read_bytes())
            self.assertEqual((a / "trial_metrics.csv").read_bytes(), (b / "trial_metrics.csv").read_bytes())

class LifecycleTests(unittest.TestCase):
    def test_recovery_state_logged(self):
        engine = RobotMissionEngine(load(SCENARIOS / "demo_recovery.json"))
        engine.run()
        phases = [e["current"] for e in engine.recorder.events if e["event"] == "MISSION_PHASE"]
        self.assertIn("RECOVERING", phases)
        self.assertEqual(phases[-1], "SUCCEEDED")

    def test_invalid_state_transition(self):
        from warehouse_robot.lifecycle import MissionLifecycle, Phase
        state = MissionLifecycle()
        ctx = Context(World(2, 2, set(), {}), Robot((0, 0)),
                      Mission("x", "box", (0, 0), (1, 1)), Recorder(), {}, {})
        with self.assertRaisesRegex(ValueError, "invalid transition"):
            state.transition(Phase.SUCCEEDED, ctx)
