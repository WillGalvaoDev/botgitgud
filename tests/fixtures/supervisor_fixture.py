"""Tiny standalone script used ONLY by tests/unit/test_ops_supervisor.py to
prove behavior against a real Windows subprocess — never the real bot, never
Discord, never WCL. Not part of the shipped package.

Modes:
    report-cwd <marker_path>   write os.getcwd() to marker_path, exit 0
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[1] == "report-cwd":
        marker = Path(argv[2])
        marker.write_text(str(Path.cwd()), encoding="utf-8")
        return 0
    sys.stderr.write(f"modo desconhecido: {argv[1:]!r}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
