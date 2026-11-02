"""Command-line interface: scenario replay, fault tests, and production scorecards."""
from __future__ import annotations

import argparse
from pathlib import Path
import json
import sys
import random
import csv
import statistics

from .engine import RobotMissionEngine
from .scenario import load


ROOT = Path(__file__).resolve().parent.parent


def execute(path: Path, out: Path | None = None) -> dict:
    engine = RobotMissionEngine(load(path))
    summary = engine.run()
    if out:
        engine.export(out, summary)
    return summary


def print_summary(summary: dict) -> None:
    counts = summary["counters"]
    print(f"\n{summary['scenario']} — {summary['result']}")
    print(f"  missions: {summary['successes']} succeeded, {summary['failures']} failed "
          f"({summary['completion_rate']:.0%} completion)")
    print(f"  ticks: {summary['total_ticks']} | steps: {counts['robot_steps']} "
          f"| retries: {counts['retries']} | replans: {counts['replans']} "
          f"| injected faults: {counts['faults_injected']}")
    for row in summary["missions"]:
        info = f" — {row['reason']}" if row["reason"] else ""
        print(f"    {row['mission']}: {row['status']} in {row['duration_ticks']} ticks{info}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Python-only warehouse robot skill validation")
    sub = parser.add_subparsers(dest="cmd", required=True)
    demo = sub.add_parser("demo", help="Run the recovery demonstration")
    demo.add_argument("--out", type=Path, default=Path("output/demo"))
    run = sub.add_parser("run", help="Run a custom JSON scenario")
    run.add_argument("scenario", type=Path)
    run.add_argument("--out", type=Path, default=Path("output/run"))
    suite = sub.add_parser("suite", help="Run all regression scenarios with asserted expectations")
    suite.add_argument("--out", type=Path, default=Path("output/suite"))
    stress = sub.add_parser("stress", help="Deterministic fault-injection validation campaign")
    stress.add_argument("--trials", type=int, default=100)
    stress.add_argument("--seed", type=int, default=42)
    stress.add_argument("--fault-probability", type=float, default=0.2)
    stress.add_argument("--out", type=Path, default=Path("output/stress"))
    args = parser.parse_args(argv)
    if args.cmd == "stress":
        if args.trials < 1 or not 0 <= args.fault_probability <= 1:
            parser.error("trials must be positive; fault probability must be 0..1")
        rng = random.Random(args.seed)
        rows = []
        for trial in range(1, args.trials + 1):
            scenario = load(ROOT / "scenarios" / "demo_recovery.json")
            scenario.name = f"stress-run-{trial:03d}"
            # Sample independent invocation failures, including sequential failures
            # that can exhaust the retry policy. Fixed seed makes runs reproducible.
            scenario.faults = {}
            for m in scenario.missions:
                for action in ("navigate_pickup", "grasp", "navigate_dropoff", "place"):
                    attempts = {n for n in range(1, scenario.retry_budget + 1)
                                if rng.random() < args.fault_probability}
                    if attempts:
                        scenario.faults[f"{m.mission_id}:{action}"] = attempts
            result = RobotMissionEngine(scenario).run()
            rows.append({
                "trial": trial, "passed": result["result"] == "PASS",
                "successes": result["successes"], "ticks": result["total_ticks"],
                "retries": result["counters"]["retries"],
                "replans": result["counters"]["replans"],
                "faults": result["counters"]["faults_injected"],
            })
        passes = sum(r["passed"] for r in rows)
        sorted_ticks = sorted(r["ticks"] for r in rows)
        p95 = sorted_ticks[max(0, (95 * len(sorted_ticks) + 99) // 100 - 1)]
        scorecard = {
            "model": "DISCRETE SOFTWARE SIMULATION ONLY; not physical robot reliability",
            "seed": args.seed, "trials": args.trials,
            "fault_probability_per_invocation": args.fault_probability,
            "fully_completed_trials": passes,
            "full_mission_success_rate": round(passes / args.trials, 4),
            "p95_trial_ticks": p95,
            "mean_trial_ticks": round(statistics.mean(r["ticks"] for r in rows), 2),
            "mean_retries": round(statistics.mean(r["retries"] for r in rows), 2),
            "mean_injected_faults": round(statistics.mean(r["faults"] for r in rows), 2),
        }
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "stress_scorecard.json").write_text(json.dumps(scorecard, indent=2) + "\n")
        with (args.out / "trial_metrics.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(json.dumps(scorecard, indent=2))
        return 0
    if args.cmd == "demo":
        result = execute(ROOT / "scenarios" / "demo_recovery.json", args.out)
        print_summary(result)
        return 0 if result["result"] == "PASS" else 1
    if args.cmd == "run":
        result = execute(args.scenario, args.out)
        print_summary(result)
        return 0 if result["result"] == "PASS" else 1
    folder = ROOT / "scenarios"
    verdicts = []
    for scenario_path in sorted(folder.glob("*.json")):
        scenario = load(scenario_path)
        result = execute(scenario_path, args.out / scenario_path.stem)
        print_summary(result)
        met_expectation = (scenario.expected_successes is not None
                           and result["successes"] == scenario.expected_successes)
        verdicts.append({"scenario": scenario.name,
                         "expected_successes": scenario.expected_successes,
                         "actual_successes": result["successes"],
                         "checks_passed": met_expectation})
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "regression_scorecard.json").write_text(json.dumps(verdicts, indent=2) + "\n")
    passed = sum(v["checks_passed"] for v in verdicts)
    print(f"\nREGRESSION SCORECARD: {passed}/{len(verdicts)} scenario expectations met")
    return 0 if passed == len(verdicts) else 1


if __name__ == "__main__":
    sys.exit(main())
