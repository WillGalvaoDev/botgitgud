"""Trusted fixed commands; no agent-provided shell commands are executed."""

from __future__ import annotations

import json
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .protocol import Blocked
from .storage import digest
from .workspace import clean_environment, tree


@dataclass(frozen=True)
class GateReport:
    tree_sha: str
    commands: tuple[tuple[str, ...], ...]
    exit_codes: tuple[int, ...]
    passed_tests: frozenset[str]
    artifacts: tuple[tuple[str, str], ...]

    def valid(self, expected_tree: str) -> bool:
        return (
            self.tree_sha == expected_tree
            and len(self.commands) == 4
            and self.exit_codes == (0, 0, 0, 0)
            and bool(self.passed_tests)
            and len(self.artifacts) >= 5
            and all(
                Path(p).is_file() and digest(Path(p).read_bytes()) == sha
                for p, sha in self.artifacts
            )
        )


class MechanicalGates:
    def __init__(self, python: Path, environment_root: Path, timeout: int = 1800) -> None:
        self.python = python.resolve()
        self.environment_root = environment_root.resolve()
        self.timeout = timeout

    def run(self, root: Path, output: Path) -> GateReport:
        output.mkdir(parents=True, exist_ok=True)
        before = tree(root)
        junit = output / "pytest.xml"
        type_config = output / "pyright.json"
        type_config.write_text(
            json.dumps(
                {
                    "include": [str(root / "src"), str(root / "tests")],
                    "exclude": [str(root / "legacy")],
                    "typeCheckingMode": "standard",
                    "extraPaths": [str(root / "tests/fixtures"), str(root / "src")],
                    "venvPath": str(self.environment_root),
                    "venv": ".venv",
                }
            ),
            encoding="utf-8",
        )
        prefix = (str(self.python), "-B", "-m")
        commands = (
            (*prefix, "ruff", "check", "."),
            (*prefix, "ruff", "format", "--check", "."),
            (*prefix, "pyright", "--project", str(type_config), "--pythonpath", str(self.python)),
            (
                *prefix,
                "pytest",
                "-o",
                "addopts=",
                "-m",
                "not network",
                "-p",
                "no:cacheprovider",
                "-p",
                "syrupy",
                "-p",
                "hypothesis.extra.pytestplugin",
                "--junitxml",
                str(junit),
                "tests",
            ),
        )
        exits = []
        artifacts = []
        for index, command in enumerate(commands):
            path = output / f"gate-{index}.txt"
            try:
                result = subprocess.run(
                    command,
                    cwd=root,
                    env=clean_environment(root),
                    capture_output=True,
                    timeout=self.timeout,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                path.write_text(str(exc), encoding="utf-8")
                raise Blocked(f"Mechanical gate could not run: {path}") from exc
            path.write_bytes(result.stdout + b"\nSTDERR\n" + result.stderr)
            artifacts.append((str(path), digest(path.read_bytes())))
            exits.append(result.returncode)
        passed = set()
        if junit.is_file():
            try:
                document = ET.parse(junit)
                for case in document.iter("testcase"):
                    if any(case.find(tag) is not None for tag in ("failure", "error", "skipped")):
                        continue
                    classname = case.attrib.get("classname", "")
                    # Standard module-level pytest tests; class tests use explicit ::Class.
                    parts = classname.split(".")
                    test_index = next(
                        (i for i, part in enumerate(parts) if part.startswith("test_")),
                        len(parts) - 1,
                    )
                    module = "/".join(parts[: test_index + 1]) + ".py"
                    suffix = "::".join([*parts[test_index + 1 :], case.attrib.get("name", "")])
                    passed.add(module + "::" + suffix)
            except (ET.ParseError, OSError) as exc:
                raise Blocked("Invalid mechanical JUnit evidence") from exc
            artifacts.append((str(junit), digest(junit.read_bytes())))
        if before != tree(root):
            raise Blocked("Mechanical gates mutated versioned inputs")
        return GateReport(before, commands, tuple(exits), frozenset(passed), tuple(artifacts))
