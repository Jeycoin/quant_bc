"""Experiment lifecycle CLI: start / stop / list / report.

Usage:
  python scripts/experiment.py start "7d-testnet" --symbols BTC,ETH --strategies grid
  python scripts/experiment.py stop <experiment_id> [--status completed|aborted]
  python scripts/experiment.py list
  python scripts/experiment.py report <experiment_id> [-o report.md]

While an experiment is 'running', every agent decision event is tagged with
its experiment_id automatically (agent/agent.py reads get_active_experiment).
Do not change prompt/config mid-experiment — it breaks comparability; the
version stamps make such changes visible.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from analytics.store import AnalyticsStore
from analytics.versioning import current_versions

DB = str(REPO_ROOT / "data" / "analytics.db")


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Quant Lab experiment CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_start = sub.add_parser("start")
    p_start.add_argument("name")
    p_start.add_argument("--symbols", default="BTC,ETH")
    p_start.add_argument("--strategies", default="grid")
    p_start.add_argument("--notes", default=None)

    p_stop = sub.add_parser("stop")
    p_stop.add_argument("experiment_id")
    p_stop.add_argument("--status", default="completed",
                        choices=["completed", "aborted"])

    sub.add_parser("list")

    p_report = sub.add_parser("report")
    p_report.add_argument("experiment_id")
    p_report.add_argument("-o", "--output", default=None)

    args = parser.parse_args()
    store = AnalyticsStore(DB)
    try:
        if args.cmd == "start":
            if store.get_active_experiment():
                print("error: an experiment is already running; stop it first")
                sys.exit(1)
            import yaml
            config_snapshot = {}
            cfg_path = REPO_ROOT / "config" / "settings.yaml"
            if cfg_path.exists():
                config_snapshot = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            eid = store.start_experiment(
                name=args.name,
                symbols=[s.strip() for s in args.symbols.split(",") if s.strip()],
                strategies=[s.strip() for s in args.strategies.split(",") if s.strip()],
                config_snapshot=config_snapshot,
                notes=args.notes,
                **current_versions(),
            )
            print(f"started experiment {eid} ({args.name})")
        elif args.cmd == "stop":
            store.end_experiment(args.experiment_id, status=args.status)
            print(f"experiment {args.experiment_id} -> {args.status}")
        elif args.cmd == "list":
            from analytics.queries import list_experiments
            for e in list_experiments(DB):
                print(f"{e['experiment_id']}  {e['status']:10s}  {e['name']}"
                      f"  prompt={e['prompt_version']} agent={e['agent_version']}")
        elif args.cmd == "report":
            from analytics.report import generate_report
            text = generate_report(DB, args.experiment_id)
            if args.output:
                Path(args.output).write_text(text, encoding="utf-8")
                print(f"report written to {args.output}")
            else:
                print(text)
    finally:
        store.close()


if __name__ == "__main__":
    main()
