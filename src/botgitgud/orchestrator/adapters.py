"""CLI-specific discovery/transport; no provider invocation occurs at import."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .protocol import SCHEMA, STATUSES, Blocked, strict_json
from .storage import canonical


@dataclass(frozen=True)
class Request:
    role: str
    unit: str
    spec_sha: str
    prompt: str
    workspace: Path
    output: Path


class Adapter(Protocol):
    def run(self, request: Request) -> str: ...


def executable(name: str) -> tuple[str, ...]:
    located = shutil.which(name)
    if not located:
        raise Blocked(f"Interface missing from PATH: {name}")
    path = Path(located)
    if os.name == "nt" and path.suffix.lower() in {".cmd", ".ps1", ".bat"}:
        if name == "claude":
            native = path.parent / "node_modules/@anthropic-ai/claude-code/bin/claude.exe"
            if native.is_file():
                return (str(native),)
        if name == "codex":
            script = path.parent / "node_modules/@openai/codex/bin/codex.js"
            node = shutil.which("node")
            if script.is_file() and node:
                return (node, str(script))
        raise Blocked(f"No verified native launcher for {path}; refusing shell interpolation")
    return (located,)


def probe(command: tuple[str, ...]) -> str:
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Blocked(f"Interface probe failed: {command[0]}: {exc}") from exc
    if result.returncode:
        raise Blocked(f"Interface probe exit {result.returncode}: {command[0]}")
    return result.stdout + result.stderr


def discover() -> dict[str, Any]:
    claude, codex = executable("claude"), executable("codex")
    ch = probe((*claude, "--help"))
    cx = probe((*codex, "exec", "--help"))
    for flag in (
        "--json-schema",
        "--output-format",
        "--safe-mode",
        "--tools",
        "--permission-mode",
        "--no-session-persistence",
        "--model",
    ):
        if flag not in ch:
            raise Blocked(f"Claude interface lacks {flag}")
    for flag in (
        "--output-schema",
        "--output-last-message",
        "--sandbox",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--json",
    ):
        if flag not in cx:
            raise Blocked(f"Codex interface lacks {flag}")
    if "shell_tool" not in probe((*codex, "features", "list")):
        raise Blocked("Cannot determine Codex shell tool control")
    if "--ask-for-approval" not in probe((*codex, "--help")):
        raise Blocked("Cannot determine noninteractive Codex approval policy")
    auth = strict_json(probe((*claude, "auth", "status")))
    if not isinstance(auth, dict) or auth.get("loggedIn") is not True:
        raise Blocked("Claude authentication unavailable")
    login = probe((*codex, "login", "status"))
    if "Logged in" not in login:
        raise Blocked("Codex authentication unavailable")
    cache = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "models_cache.json"
    if not cache.is_file():
        raise Blocked("Cannot verify Astra: local model catalog absent")
    models = strict_json(cache.read_text(encoding="utf-8")).get("models", [])
    if not any(model.get("slug") == "gpt-6-astra" for model in models):
        raise Blocked("gpt-6-astra is not in the local Codex model catalog")
    return {
        "claude": list(claude),
        "codex": list(codex),
        "claude_version": probe((*claude, "--version")).strip(),
        "codex_version": probe((*codex, "--version")).strip(),
        "models": {"opus": "opus", "sonnet": "sonnet", "astra": "gpt-6-astra"},
        "authenticated": True,
        "generation_tested": False,
        "claude_help": ch,
        "codex_exec_help": cx,
    }


class CLIAdapter:
    def __init__(self, interfaces: dict[str, Any], timeout: int = 1800) -> None:
        self.interfaces = interfaces
        self.timeout = timeout

    def command(self, request: Request) -> list[str]:
        if request.role in {"opus", "sonnet"}:
            # Read-only tools for both: proposed files are applied only by the controller.
            return [
                *self.interfaces["claude"],
                "-p",
                "--model",
                request.role,
                "--safe-mode",
                "--no-session-persistence",
                "--permission-mode",
                "dontAsk",
                "--tools",
                "Read,Glob,Grep",
                "--allowedTools",
                "Read,Glob,Grep",
                "--output-format",
                "json",
                "--json-schema",
                canonical(SCHEMA),
            ]
        return [
            *self.interfaces["codex"],
            "--ask-for-approval",
            "never",
            "exec",
            "--model",
            "gpt-6-astra",
            "--ignore-user-config",
            "--ignore-rules",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--disable",
            "shell_tool",
            "--disable",
            "unified_exec",
            "--json",
            "--output-schema",
            str(request.output / "schema.json"),
            "--output-last-message",
            str(request.output / "final.json"),
            "-",
        ]

    def run(self, request: Request) -> str:
        request.output.mkdir(parents=True, exist_ok=True)
        (request.output / "schema.json").write_text(canonical(SCHEMA), encoding="utf-8")
        command = self.command(request)
        (request.output / "command.json").write_text(canonical(command), encoding="utf-8")
        try:
            result = subprocess.run(
                command,
                input=request.prompt.encode(),
                cwd=request.workspace,
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            (request.output / "failure.json").write_text(
                canonical({"error": type(exc).__name__, "timeout_seconds": self.timeout}),
                encoding="utf-8",
            )
            raise Blocked(f"Agent interface failed: {type(exc).__name__}") from exc
        (request.output / "stdout.txt").write_bytes(result.stdout)
        (request.output / "stderr.txt").write_bytes(result.stderr)
        if result.returncode:
            raise Blocked(f"Agent interface exit {result.returncode}; see {request.output}")
        raw = result.stdout.decode("utf-8")
        if request.role != "astra":
            envelope = strict_json(raw)
            if (
                not isinstance(envelope, dict)
                or envelope.get("type") != "result"
                or envelope.get("subtype") != "success"
                or envelope.get("is_error") is not False
                or not isinstance(envelope.get("structured_output"), dict)
            ):
                raise Blocked("Claude transport did not return successful structured_output")
            value = envelope["structured_output"]
            # A second explicit status in the provider's final text must agree.
            if envelope.get("result") in STATUSES and envelope["result"] != value.get("status"):
                raise Blocked("Claude final status contradicts structured_output")
            return canonical(value)
        events = [strict_json(line) for line in raw.splitlines() if line.strip()]
        if (
            not all(isinstance(event, dict) for event in events)
            or any(e.get("type") in {"error", "turn.failed"} for e in events)
            or sum(e.get("type") == "turn.completed" for e in events) != 1
        ):
            raise Blocked("Codex transport incomplete or contradictory")
        final = request.output / "final.json"
        if not final.is_file():
            raise Blocked("Codex structured final output missing")
        value = strict_json(final.read_text(encoding="utf-8"))
        messages = [
            e["item"]["text"]
            for e in events
            if e.get("type") == "item.completed"
            and isinstance(e.get("item"), dict)
            and e["item"].get("type") == "agent_message"
        ]
        if not messages or strict_json(messages[-1]) != value:
            raise Blocked("Codex event/final output mismatch")
        return canonical(value)
