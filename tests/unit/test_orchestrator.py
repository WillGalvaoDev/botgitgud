"""Contract counterexamples, all agent/gate calls deterministic and offline."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from botgitgud.orchestrator.adapters import CLIAdapter, Request
from botgitgud.orchestrator.engine import Engine
from botgitgud.orchestrator.gates import GateReport
from botgitgud.orchestrator.protocol import Blocked, parse
from botgitgud.orchestrator.roadmap import UNITS, load, next_unit
from botgitgud.orchestrator.storage import canonical, digest, lease
from botgitgud.orchestrator.workspace import git, safe_path, tree, write

REPO = Path(__file__).resolve().parents[2]


def payload(request: Request, **updates: Any) -> dict[str, Any]:
    value = {
        "unit": request.unit,
        "spec_sha": request.spec_sha,
        "reason": "fixture evidence",
        "status": "HUMAN_BLOCK",
        "route": "",
        "spec": "",
        "criteria": [],
        "write_paths": [],
        "files": [],
        "evidence": [],
        "findings": [],
    }
    value.update(updates)
    return value


class FakeGates:
    def __init__(self, fail: bool = False, skipped: bool = False) -> None:
        self.fail = fail
        self.skipped = skipped
        self.calls = 0

    def run(self, root: Path, output: Path) -> GateReport:
        self.calls += 1
        artifacts = []
        for index in range(5):
            path = output / f"{index}.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("controller-generated evidence")
            artifacts.append((str(path), digest(path.read_bytes())))
        return GateReport(
            tree(root),
            (("ruff",), ("format",), ("pyright",), ("pytest",)),
            (0, 0, 0, 1 if self.fail else 0),
            frozenset() if self.skipped else frozenset({"tests/test_unit.py::test_fact"}),
            tuple(artifacts),
        )


class FakeAgents:
    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = responses or []
        self.calls: list[Request] = []

    def run(self, request: Request) -> str:
        self.calls.append(request)
        action = self.responses.pop(0) if self.responses else "happy"
        if action == "interrupt":
            raise KeyboardInterrupt
        if action == "invalid":
            return "SPEC_READY"
        if action in {"HUMAN_BLOCK", "TECH_BLOCK"}:
            return canonical(payload(request, status=action))
        if action == "direct_write":
            write(request.workspace, "src/tampered.py", "bad")
        if request.role == "opus":
            return canonical(
                payload(
                    request,
                    status="SPEC_READY",
                    spec=f"SPEC revision {len(self.calls)}: {request.unit}",
                    criteria=[{"id": f"A{i}", "text": f"Fact {i}"} for i in range(3)],
                    write_paths=[
                        "src/botgitgud/unit.py",
                        "tests/test_unit.py",
                        "docs/evidence.txt",
                    ],
                )
            )
        if request.role == "sonnet":
            result = payload(
                request,
                status="IMPLEMENTATION_READY",
                files=[
                    {"path": "src/botgitgud/unit.py", "content": f"VALUE = {len(self.calls)}\n"},
                    {
                        "path": "tests/test_unit.py",
                        "content": "def test_fact():\n    assert True\n",
                    },
                    {"path": "docs/evidence.txt", "content": f"Evidence {len(self.calls)}"},
                ],
                evidence=[
                    {
                        "criterion": f"A{i}",
                        "test": "tests/test_unit.py::test_fact",
                        "inputs": "fixture",
                        "expected": "1",
                        "observed": "1",
                        "artifact": "docs/evidence.txt",
                    }
                    for i in range(3)
                ],
            )
            if action == "empty":
                result["files"] = []
            if action == "same":
                result["files"] = [{"path": "src/botgitgud/unit.py", "content": "VALUE = 0\n"}]
            if action == "spec_write":
                result["files"][0]["path"] = "docs/submilestones/M2.1/spec-v001.md"
            if action == "missing_evidence":
                result["evidence"] = result["evidence"][:1]
            return canonical(result)
        if action in {"implementation", "specification"}:
            return canonical(
                payload(
                    request,
                    status="REQUIRES_CHANGES",
                    route=action,
                    findings=[
                        {
                            "basis": "criterion",
                            "reference": "A0",
                            "counterexample": "input X violates A0",
                        }
                    ],
                )
            )
        if action == "contradictory":
            return canonical(
                payload(
                    request,
                    status="MILESTONE_CLOSED",
                    findings=[
                        {
                            "basis": "criterion",
                            "reference": "A0",
                            "counterexample": "still broken",
                        }
                    ],
                )
            )
        return canonical(payload(request, status="MILESTONE_CLOSED"))


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    repo = tmp_path / "repository"
    repo.mkdir()
    git(repo, "init", "-q")
    for name in [
        "milestone-workflow.md",
        "methodology-roadmap-m2-m6.md",
        "m1-closure.md",
        "m0-methodology-contract.md",
        "m1-specification.md",
    ]:
        write(repo, "docs/" + name, (REPO / "docs" / name).read_text(encoding="utf-8"))
    write(repo, "src/botgitgud/unit.py", "VALUE = 0\n")
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@localhost",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "fixture",
    )
    result = Engine(repo, tmp_path / "state", FakeAgents(), FakeGates())
    result.start(authorized=True)
    return result


def test_happy_path_all_16_and_macro_closures(engine: Engine) -> None:
    before = tree(engine.repository)
    result = engine.run()
    assert result["status"] == "PROJECT_COMPLETE"
    assert result["closed"] == ["M1", *UNITS]
    assert result["macros_closed"] == ["M1", "M2", "M3", "M4", "M5", "M6"]
    assert tree(engine.repository) == before
    events = engine.store.events()
    assert sum(e["event"] == "MILESTONE_CLOSED" for e in events) == 16
    assert events[-2]["event"] == "MILESTONE_CLOSED"
    assert events[-2]["state"]["unit"] == "M6.3"
    assert events[-1]["event"] == "PROJECT_COMPLETE"


@pytest.mark.parametrize(
    "route,expected", [("implementation", "sonnet"), ("specification", "opus")]
)
def test_correction_routes_and_revision(engine: Engine, route: str, expected: str) -> None:
    agents = FakeAgents(["happy", "happy", route, "happy"])
    engine.adapter = agents
    engine.step()
    sha = engine.store.read()["spec_sha"]  # type: ignore[index]
    engine.step()
    state = engine.step()
    assert state["role"] == expected
    assert state["closed"] == ["M1"]
    engine.step()
    assert agents.calls[-1].role == expected
    context = json.loads(agents.calls[-1].prompt)
    assert context["previous_review"]["route"] == route
    if expected == "opus":
        assert engine.store.read()["spec_sha"] != sha  # type: ignore[index]


@pytest.mark.parametrize(
    "actions,status",
    [
        (["HUMAN_BLOCK"], "HUMAN_BLOCK"),
        (["happy", "TECH_BLOCK"], "TECH_BLOCK"),
        (["invalid"], "TECH_BLOCK"),
        (["happy", "happy", "contradictory"], "TECH_BLOCK"),
        (["happy", "empty"], "TECH_BLOCK"),
        (["happy", "same"], "TECH_BLOCK"),
        (["happy", "missing_evidence"], "TECH_BLOCK"),
        (["happy", "spec_write"], "TECH_BLOCK"),
        (["direct_write"], "TECH_BLOCK"),
    ],
)
def test_fail_closed(engine: Engine, actions: list[str], status: str) -> None:
    agents = FakeAgents(actions.copy())
    engine.adapter = agents
    assert engine.run()["status"] == status
    assert len(agents.calls) == len(actions)
    assert engine.store.read()["closed"] == ["M1"]  # type: ignore[index]


@pytest.mark.parametrize("role", ["opus", "sonnet", "astra"])
def test_human_block_preserves_original_cause_for_every_role(engine: Engine, role: str) -> None:
    reason = "Required product decision is outside this agent's workflow authority."

    class HumanBlockedAgents(FakeAgents):
        def run(self, request: Request) -> str:
            if request.role != role:
                return super().run(request)
            self.calls.append(request)
            raw = canonical(payload(request, status="HUMAN_BLOCK", reason=reason))
            accepted = parse(raw, request.role, request.unit, request.spec_sha)
            assert accepted["status"] == "HUMAN_BLOCK"
            assert accepted["reason"] == reason
            return raw

    agents = HumanBlockedAgents()
    engine.adapter = agents
    state = engine.run()
    assert state["status"] == "HUMAN_BLOCK"
    assert state["reason"] == reason
    assert state["closed"] == ["M1"]
    assert [request.role for request in agents.calls] == ["opus", "sonnet", "astra"][
        : ["opus", "sonnet", "astra"].index(role) + 1
    ]
    if role != "opus":
        assert agents.calls[-1].spec_sha == state["spec_sha"] != ""
    event = engine.store.events()[-1]
    assert event["event"] == "HUMAN_BLOCK"
    assert event["state"]["role"] == role
    assert event["details"]["reason"] == event["state"]["reason"] == reason
    calls_before_resume = len(agents.calls)
    assert engine.run()["status"] == "HUMAN_BLOCK"
    assert len(agents.calls) == calls_before_resume


def test_sonnet_cannot_close_milestone_even_with_valid_unit_and_spec(engine: Engine) -> None:
    class InvalidSonnetAgents(FakeAgents):
        def run(self, request: Request) -> str:
            if request.role != "sonnet":
                return super().run(request)
            self.calls.append(request)
            raw = canonical(payload(request, status="MILESTONE_CLOSED"))
            with pytest.raises(Blocked, match="Wrong role status or unit"):
                parse(raw, request.role, request.unit, request.spec_sha)
            return raw

    agents = InvalidSonnetAgents()
    engine.adapter = agents
    state = engine.run()
    assert state["status"] == "TECH_BLOCK"
    assert state["reason"] == "Wrong role status or unit"
    assert state["closed"] == ["M1"]
    assert [request.role for request in agents.calls] == ["opus", "sonnet"]


@pytest.mark.parametrize("skipped", [False, True])
def test_no_advance_without_actual_passing_gates(engine: Engine, skipped: bool) -> None:
    agents = FakeAgents()
    engine.adapter = agents
    engine.gates = FakeGates(fail=not skipped, skipped=skipped)
    engine.max_attempts = 3
    assert engine.run()["status"] == "TECH_BLOCK"
    assert [r.role for r in agents.calls] == (
        ["opus", "sonnet"] if skipped else ["opus", "sonnet", "sonnet"]
    )


def test_restart_resume_uses_persisted_spec_without_repeating_opus(engine: Engine) -> None:
    engine.step()
    agents = FakeAgents()
    resumed = Engine(engine.repository, engine.store.root, agents, FakeGates())
    result = resumed.step()
    assert agents.calls[0].role == "sonnet"
    assert result["role"] == "astra"
    assert agents.calls[0].spec_sha


def test_interrupted_attempt_never_blindly_reexecutes(engine: Engine) -> None:
    agents = FakeAgents(["interrupt"])
    engine.adapter = agents
    with pytest.raises(KeyboardInterrupt):
        engine.step()
    resumed = Engine(engine.repository, engine.store.root, agents, FakeGates())
    assert resumed.run()["status"] == "TECH_BLOCK"
    assert len(agents.calls) == 1
    assert "Interrupted" in resumed.store.read()["reason"]  # type: ignore[index]


def test_dependency_not_closed() -> None:
    with pytest.raises(Blocked, match="Dependency not CLOSED"):
        next_unit(load(REPO), set())


def test_lease_prevents_second_writer(engine: Engine) -> None:
    with lease(engine.lock), pytest.raises(Blocked, match="lease"):
        engine.step()


def test_no_second_start_or_unauthorized_start(engine: Engine) -> None:
    with pytest.raises(Blocked, match="authorization"):
        engine.start()
    with pytest.raises(Blocked, match="already exists"):
        engine.start(authorized=True)


@pytest.mark.parametrize(
    "path",
    ["../outside", "C:/outside", "/outside", "docs/../../x", "docs/\\outside", ".git/config"],
)
def test_paths_cannot_escape(tmp_path: Path, path: str) -> None:
    with pytest.raises(Blocked):
        safe_path(tmp_path, path)


def test_duplicate_tokens_and_wrong_role_fail_closed(tmp_path: Path) -> None:
    request = Request("opus", "M2.1", "", "", tmp_path, tmp_path)
    with pytest.raises(Blocked):
        parse('{"status":"SPEC_READY","status":"HUMAN_BLOCK"}', "opus", "M2.1", "")
    with pytest.raises(Blocked, match="role"):
        parse(canonical(payload(request, status="MILESTONE_CLOSED")), "opus", "M2.1", "")


def test_adapter_commands_use_verified_interfaces_and_no_model_execution(tmp_path: Path) -> None:
    adapter = CLIAdapter({"claude": ["claude-native"], "codex": ["codex-native"]})
    for role in ("opus", "sonnet", "astra"):
        command = adapter.command(Request(role, "M2.1", "", "", tmp_path, tmp_path))
        assert "--model" in command
        assert "--resume" not in command and "--continue" not in command
        if role == "astra":
            assert command[command.index("--sandbox") + 1] == "read-only"
            assert "gpt-6-astra" in command
            assert "shell_tool" in command
        else:
            assert command[command.index("--tools") + 1] == "Read,Glob,Grep"


def test_tampered_evidence_and_tree_block(engine: Engine) -> None:
    engine.step()
    state = engine.step()
    Path(state["gates"]["artifacts"][0][0]).write_text("tampered")
    assert engine.step()["status"] == "TECH_BLOCK"


def test_audit_detects_state_tamper(engine: Engine) -> None:
    with engine.store.connect() as connection:
        connection.execute("UPDATE state SET body='{}'")
    with pytest.raises(Blocked, match="audit"):
        engine.step()


def test_recovery_uses_last_committed_checkpoint_and_records_resolution(engine: Engine) -> None:
    engine.adapter = FakeAgents(["happy", "invalid"])
    engine.step()
    previous = engine.store.read()
    assert engine.step()["status"] == "TECH_BLOCK"
    state = engine.retry("Transport fixed; interrupted process terminated and inspected")
    assert state["spec_sha"] == previous["spec_sha"]  # type: ignore[index]
    assert state["role"] == "sonnet" and not state["pending"]
    assert engine.store.events()[-1]["event"] == "HUMAN_RECOVERY"
    agents = FakeAgents(["TECH_BLOCK"])
    engine.adapter = agents
    engine.step()
    assert "Transport fixed" in json.loads(agents.calls[-1].prompt)["human_resolution"]


@pytest.mark.parametrize("mode", ["ok", "error", "contradiction", "missing", "exit"])
def test_claude_transport_without_real_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    request = Request("opus", "M2.1", "", "fixture", tmp_path, tmp_path / "output")
    envelope = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "structured_output": payload(request),
        "result": "",
    }
    if mode == "error":
        envelope["is_error"] = True
    if mode == "contradiction":
        envelope["result"] = "SPEC_READY"
    if mode == "missing":
        del envelope["structured_output"]

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            args[0], 1 if mode == "exit" else 0, canonical(envelope).encode(), b""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = CLIAdapter({"claude": ["fake"], "codex": ["fake"]})
    if mode == "ok":
        assert json.loads(adapter.run(request))["status"] == "HUMAN_BLOCK"
    else:
        with pytest.raises(Blocked):
            adapter.run(request)


@pytest.mark.parametrize("mode", ["ok", "failed", "mismatch", "missing_final", "two_turns"])
def test_astra_transport_without_real_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    request = Request("astra", "M2.1", "sha", "fixture", tmp_path, tmp_path / "output")
    final = canonical(payload(request, status="MILESTONE_CLOSED"))

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if mode != "missing_final":
            (request.output / "final.json").write_text(final, encoding="utf-8")
        message = final if mode != "mismatch" else canonical(payload(request))
        events = [
            {"type": "item.completed", "item": {"type": "agent_message", "text": message}},
            {"type": "turn.failed" if mode == "failed" else "turn.completed"},
        ]
        if mode == "two_turns":
            events.append({"type": "turn.completed"})
        return subprocess.CompletedProcess(
            args[0], 0, "\n".join(map(canonical, events)).encode(), b""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = CLIAdapter({"claude": ["fake"], "codex": ["fake"]})
    if mode == "ok":
        assert json.loads(adapter.run(request))["status"] == "MILESTONE_CLOSED"
    else:
        with pytest.raises(Blocked):
            adapter.run(request)


def test_changed_committed_tree_cannot_resume(engine: Engine) -> None:
    state = engine.step()
    write(Path(state["workspace"]), "src/botgitgud/unit.py", "CORRUPT = True\n")
    assert engine.step()["status"] == "TECH_BLOCK"


def test_closed_macro_is_not_inferred_from_local_units(engine: Engine) -> None:
    state: dict[str, Any] = {}
    for _ in range(6):
        state = engine.step()
    assert state["closed"] == ["M1", "M2.1", "M2.2"]
    assert state["macros_closed"] == ["M1"]


def test_discovery_missing_cli_returns_tech_block(monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    from botgitgud.orchestrator.adapters import discover

    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(Blocked, match="Interface missing"):
        discover()


def test_real_mechanical_gates_on_tiny_offline_repository(tmp_path: Path) -> None:
    from botgitgud.orchestrator.gates import MechanicalGates

    root = tmp_path / "tiny"
    root.mkdir()
    git(root, "init", "-q")
    write(root, ".gitignore", "data/\n.ruff_cache/\n.hypothesis/\n__pycache__/\n")
    write(root, "src/example.py", "VALUE = 2\n")
    write(root, "tests/test_fact.py", "def test_fact() -> None:\n    assert 1 + 1 == 2\n")
    gates = MechanicalGates(Path(sys.executable), REPO)
    result = gates.run(root, tmp_path / "results")
    assert result.valid(tree(root)), result
    assert result.passed_tests == {"tests/test_fact.py::test_fact"}
    assert all("experiment" not in command for argv in result.commands for command in argv)
