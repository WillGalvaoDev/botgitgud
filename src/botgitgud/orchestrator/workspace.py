"""Private local commits; agents never publish into the user's dirty checkout."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path, PurePosixPath

from .protocol import Blocked
from .storage import canonical, digest

PROTECTED = (
    "docs/submilestones/",
    "src/botgitgud/orchestrator/",
    "tests/unit/test_orchestrator",
    "docs/reviews/",
    "docs/m1-",
    "docs/m0-",
)
AUTHORITIES = (
    "docs/milestone-workflow.md",
    "docs/methodology-roadmap-m2-m6.md",
    "docs/agent-orchestrator.md",
    "docs/warehouse-policy.md",
    "docs/implementacao.md",
    "pyproject.toml",
    ".gitignore",
)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    if result.returncode:
        raise Blocked(f"git {args[0]} failed: {result.stderr[:2000]}")
    return result.stdout.strip()


def files(root: Path) -> list[str]:
    return sorted(
        set(
            filter(
                None,
                git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split(
                    "\0"
                ),
            )
        )
    )


def safe_path(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative).parts
    if (
        not parts
        or "\\" in relative
        or ":" in relative
        or relative.startswith("/")
        or any(p in {".", "..", ".git"} for p in parts)
        or PurePosixPath(relative).as_posix() != relative.rstrip("/")
    ):
        raise Blocked(f"Unsafe path: {relative}")
    if any(
        part.endswith((".", " "))
        or part.split(".")[0].upper()
        in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }
        for part in parts
    ):
        raise Blocked(f"Ambiguous platform path: {relative}")
    candidate = root
    for part in parts:
        candidate = candidate / part
        if candidate.is_symlink() or (
            candidate.exists() and bool(getattr(candidate.lstat(), "st_file_attributes", 0) & 0x400)
        ):
            raise Blocked(f"Link/reparse path is not permitted: {relative}")
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise Blocked("Path escaped workspace")
    return candidate


def writable(path: str) -> bool:
    normalized = path.casefold()
    if normalized in {p.casefold() for p in AUTHORITIES} or normalized.startswith(PROTECTED):
        return False
    return path.startswith(("src/botgitgud/", "tests/", "docs/")) or path == "README.md"


def manifest(root: Path) -> dict[str, str]:
    result = {}
    for relative in files(root):
        path = safe_path(root, relative)
        if path.is_file():
            result[relative] = digest(path.read_bytes())
    return result


def tree(root: Path) -> str:
    return digest(canonical(manifest(root)).encode())


def commit(root: Path, message: str) -> str:
    git(root, "add", "--all")
    git(
        root,
        "-c",
        "user.name=Milestone Orchestrator",
        "-c",
        "user.email=orchestrator@localhost",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--allow-empty",
        "-qm",
        message,
    )
    return git(root, "rev-parse", "HEAD")


def snapshot(source: Path, destination: Path) -> str:
    destination.mkdir(parents=True, exist_ok=False)
    for relative in files(source):
        # Runtime credentials, local tool configuration and datasets never enter a stage.
        if (
            relative.startswith((".claude/", ".codex/", "data/", "backups/"))
            or (
                PurePosixPath(relative).name.startswith(".env")
                and PurePosixPath(relative).name != ".env.example"
            )
            or relative.endswith((".duckdb", ".duckdb.wal"))
        ):
            continue
        src = safe_path(source, relative)
        if not src.is_file():
            continue
        dst = safe_path(destination, relative)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
    git(destination, "init", "-q")
    return commit(destination, "Isolated input snapshot")


def write(root: Path, relative: str, content: str) -> None:
    path = safe_path(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def clean_environment(root: Path) -> dict[str, str]:
    # Gates receive no provider/Discord/WCL credentials, user PYTHONPATH or .env.
    keys = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "LANG", "LC_ALL"}
    env = {key: value for key, value in os.environ.items() if key.upper() in keys}
    env.update(
        {
            "PYTHONPATH": str(root / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "DATA_DIR": str(root / "data"),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }
    )
    gate_home = root / "data" / "gate-home"
    gate_home.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(gate_home)
    env["USERPROFILE"] = str(gate_home)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env
