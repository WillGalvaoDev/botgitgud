"""Explicit START CLI. doctor/status never execute model generations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .adapters import CLIAdapter, discover
from .engine import Engine
from .gates import MechanicalGates
from .protocol import Blocked
from .storage import Store
from .workspace import git


def main() -> int:
    parser = argparse.ArgumentParser(description="M2-M6 milestone orchestrator")
    parser.add_argument(
        "command", choices=("doctor", "status", "start", "resume", "retry", "audit")
    )
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--authorize", choices=("START",))
    parser.add_argument("--resolution", default="")
    args = parser.parse_args()
    try:
        root = args.repository.resolve()
        common = (root / git(root, "rev-parse", "--git-common-dir")).resolve()
        state_root = common / "milestone-orchestrator"
        if args.command == "doctor":
            interfaces = discover()
            # Auth probe deliberately records no account identifiers or credentials.
            print(json.dumps(interfaces, ensure_ascii=False, indent=2))
            return 0
        if args.command in {"status", "audit"}:
            if not (state_root / "state.sqlite3").exists():
                print('{"status":"NOT_STARTED"}')
                return 0
            store = Store(state_root)
            print(
                json.dumps(
                    store.read() if args.command == "status" else store.events(),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command == "start" and args.authorize != "START":
            raise Blocked("start requires --authorize START; no unit was started")
        interfaces = discover()
        adapter = CLIAdapter(interfaces)
        engine = Engine(root, state_root, adapter, MechanicalGates(Path(sys.executable), root))
        if args.command == "start":
            engine.start(authorized=True)
        elif args.command == "retry":
            engine.retry(args.resolution)
        elif engine.store.read() is None:
            raise Blocked("No authorized run exists to resume")
        result = engine.run()
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "unit": result["unit"],
                    "reason": result["reason"],
                    "state_root": str(state_root),
                    "workspace": result["workspace"],
                },
                ensure_ascii=False,
            )
        )
        return 0 if result["status"] == "PROJECT_COMPLETE" else 2
    except (Blocked, OSError, ValueError) as exc:
        print(json.dumps({"status": "TECH_BLOCK", "reason": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
