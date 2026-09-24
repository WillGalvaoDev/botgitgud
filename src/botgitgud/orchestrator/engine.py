"""Deterministic transitions around isolated, explicitly validated agent attempts."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol

from .adapters import Adapter, Request
from .gates import GateReport
from .protocol import Blocked, parse
from .roadmap import CLOSURES, UNITS, load, next_unit
from .storage import Store, canonical, digest, lease
from .workspace import commit, files, git, manifest, safe_path, snapshot, tree, writable, write

TERMINAL = {"HUMAN_BLOCK", "TECH_BLOCK", "PROJECT_COMPLETE", "OPERATIONAL_FAILURE"}


class Gates(Protocol):
    def run(self, root: Path, output: Path) -> GateReport: ...


class Engine:
    def __init__(
        self,
        repository: Path,
        state_root: Path,
        adapter: Adapter,
        gates: Gates,
        max_attempts: int = 24,
    ) -> None:
        self.repository = repository.resolve()
        self.store = Store(state_root.resolve())
        self.adapter = adapter
        self.gates = gates
        self.max_attempts = max_attempts
        common = Path(git(self.repository, "rev-parse", "--git-common-dir"))
        self.lock = (self.repository / common).resolve() / "milestone-orchestrator.lock"
        self.registry = self.lock.with_suffix(".owner")

    def start(self, *, authorized: bool = False) -> dict[str, Any]:
        if not authorized:
            raise Blocked("START authorization required")
        with lease(self.lock):
            if self.registry.exists() and self.registry.read_text() != str(self.store.root):
                raise Blocked("Repository already belongs to another orchestration run")
            if self.store.read() is not None:
                raise Blocked("Run already exists; use resume")
            self.registry.write_text(str(self.store.root))
            load(self.repository)
            if "MILESTONE_CLOSED" not in (self.repository / "docs/m1-closure.md").read_text():
                raise Blocked("M1 is not CLOSED")
            working = self.store.root / "baseline"
            base_commit = snapshot(self.repository, working)
            state = {
                "status": "RUNNING",
                "closed": ["M1"],
                "macros_closed": ["M1"],
                "unit": UNITS[0],
                "role": "opus",
                "attempt": 0,
                "unit_attempt": 0,
                "workspace": str(working),
                "commit": base_commit,
                "tree": tree(working),
                "source_commit": git(self.repository, "rev-parse", "HEAD"),
                "spec_sha": "",
                "spec_version": 0,
                "spec_path": "",
                "contract": {},
                "review": {},
                "human_resolution": "",
                "evidence": [],
                "gates": {},
                "pending": False,
                "reason": "",
                "governance": digest((working / "docs/milestone-workflow.md").read_bytes()),
                "roadmap": digest((working / "docs/methodology-roadmap-m2-m6.md").read_bytes()),
            }
            self.store.save(state, "START")
            return state

    def run(self) -> dict[str, Any]:
        while True:
            result = self.step()
            if result["status"] in TERMINAL:
                return result

    def retry(self, resolution: str) -> dict[str, Any]:
        """Human recovery after a block; never treats a partial attempt as success."""
        if not resolution.strip():
            raise Blocked("Recovery requires a recorded resolution")
        with lease(self.lock):
            latest = self.store.read()
            if not latest or latest["status"] not in {"HUMAN_BLOCK", "TECH_BLOCK"}:
                raise Blocked("Only a blocked run can be retried")
            checkpoints = [
                e["state"]
                for e in self.store.events()
                if e["state"]["status"] == "RUNNING" and not e["state"]["pending"]
            ]
            if not checkpoints:
                raise Blocked("No committed checkpoint for recovery")
            state = json.loads(canonical(checkpoints[-1]))
            state.update(
                attempt=latest["attempt"], unit_attempt=0, reason="", human_resolution=resolution
            )
            self.store.save(state, "HUMAN_RECOVERY", {"resolution": resolution})
            return state

    def step(self) -> dict[str, Any]:
        with lease(self.lock):
            state = self.store.read()
            if state is None:
                raise Blocked("No START authorization persisted")
            if state["status"] in TERMINAL:
                return state
            try:
                return self._step(state)
            except (Blocked, OSError, UnicodeError) as exc:
                state.update(status="TECH_BLOCK", reason=str(exc))
                # Preserve pending=True after an interrupted/untrusted attempt; no blind rerun.
                self.store.save(state, "TECH_BLOCK", {"error": str(exc)})
                return state
            except (ValueError, KeyError, TypeError, RuntimeError) as exc:
                state.update(status="OPERATIONAL_FAILURE", reason=str(exc))
                self.store.save(state, "OPERATIONAL_FAILURE", {"error": str(exc)})
                return state

    def _step(self, state: dict[str, Any]) -> dict[str, Any]:
        if state["pending"]:
            raise Blocked(
                "Interrupted in-flight attempt; inspect/terminate process before recovery"
            )
        current = Path(state["workspace"])
        if tree(current) != state["tree"] or git(current, "rev-parse", "HEAD") != state["commit"]:
            raise Blocked("Committed stage differs from persisted state")
        if (
            digest((current / "docs/milestone-workflow.md").read_bytes()) != state["governance"]
            or digest((current / "docs/methodology-roadmap-m2-m6.md").read_bytes())
            != state["roadmap"]
        ):
            raise Blocked("Governance/roadmap changed during autonomous run")
        unit = next_unit(load(current), set(state["closed"]))
        if unit is None:
            state["status"] = "PROJECT_COMPLETE"
            self.store.save(state, "PROJECT_COMPLETE")
            return state
        if unit.name != state["unit"]:
            raise Blocked("State unit is not the next eligible roadmap unit")
        if state["unit_attempt"] >= self.max_attempts:
            raise Blocked("Per-unit attempt limit reached without contractual closure")
        state["attempt"] += 1
        state["unit_attempt"] += 1
        attempt = self.store.root / f"attempt-{state['attempt']:05d}"
        workspace = attempt / "workspace"
        state["pending"] = True
        self.store.save(state, "ATTEMPT_STARTED", {"agent": state["role"], "path": str(attempt)})
        snapshot(current, workspace)
        baseline = manifest(workspace)
        head = git(workspace, "rev-parse", "HEAD")
        self.store.save(
            state,
            "ATTEMPT_INPUT",
            {
                "agent": state["role"],
                "commit": head,
                "tree": tree(workspace),
                "model": {"opus": "opus", "sonnet": "sonnet", "astra": "gpt-6-astra"}[
                    state["role"]
                ],
                "path": str(attempt),
            },
        )
        prompt = self.prompt(state, workspace, unit.text)
        write(attempt, "prompt.txt", prompt)
        request = Request(
            state["role"],
            state["unit"],
            state["spec_sha"],
            prompt,
            workspace,
            attempt / "transport",
        )
        raw = self.adapter.run(request)
        write(attempt, "response.json", raw)
        if manifest(workspace) != baseline or git(workspace, "rev-parse", "HEAD") != head:
            raise Blocked(
                "Agent wrote directly; only controller-applied structured files permitted"
            )
        result = parse(raw, state["role"], state["unit"], state["spec_sha"])
        state["pending"] = False
        status = result["status"]
        if status in {"HUMAN_BLOCK", "TECH_BLOCK"}:
            state.update(status=status, reason=result["reason"])
            self.store.save(state, status, result)
            return state
        if status == "SPEC_READY":
            self.spec_ready(state, workspace, result)
        elif status == "IMPLEMENTATION_READY":
            self.implementation_ready(state, workspace, attempt, result)
            if state["role"] == "sonnet":
                status = "GATES_REJECTED"
        else:
            self.review_ready(state, result)
        state["workspace"] = str(workspace)
        state["commit"] = commit(workspace, f"{unit.name}: {status}, attempt {state['attempt']}")
        state["tree"] = tree(workspace)
        details = {
            "agent": request.role,
            "result": result,
            "response_sha": digest(raw.encode()),
            "prompt_sha": digest(prompt.encode()),
            "attempt_path": str(attempt),
            "git_tree": git(workspace, "rev-parse", "HEAD^{tree}"),
        }
        self.store.save(state, status, details)
        if state["closed"] == ["M1", *UNITS]:
            state["status"] = "PROJECT_COMPLETE"
            self.store.save(state, "PROJECT_COMPLETE")
        return state

    def spec_ready(self, state: dict[str, Any], root: Path, result: dict[str, Any]) -> None:
        for path in result["write_paths"]:
            safe_path(root, path)
            if not writable(path):
                raise Blocked(f"SPEC write scope exceeds operational authority: {path}")
        contract = {key: result[key] for key in ("spec", "criteria", "write_paths")}
        checksum = digest(canonical(contract).encode())
        if checksum == state["spec_sha"]:
            raise Blocked("Opus correction did not revise the SPEC")
        state["spec_version"] += 1
        relative = f"docs/submilestones/{state['unit']}/spec-v{state['spec_version']:03d}"
        write(root, relative + ".md", result["spec"])
        write(root, relative + ".json", canonical(contract))
        state.update(
            spec_sha=checksum,
            spec_path=relative + ".md",
            contract=contract,
            role="sonnet",
            gates={},
            evidence=[],
        )

    def implementation_ready(
        self, state: dict[str, Any], root: Path, attempt: Path, result: dict[str, Any]
    ) -> None:
        paths = [item["path"] for item in result["files"]]
        if len(paths) != len({path.casefold() for path in paths}):
            raise Blocked("Duplicate implementation path")
        before = tree(root)
        changed = []
        for item in result["files"]:
            path = item["path"]
            target = safe_path(root, path)
            scope = state["contract"]["write_paths"]
            if not writable(path) or not any(
                path == p or (p.endswith("/") and path.startswith(p)) for p in scope
            ):
                raise Blocked(
                    f"Sonnet attempted to change SPEC/governance/out-of-scope path: {path}"
                )
            if not target.exists() or target.read_text(encoding="utf-8") != item["content"]:
                changed.append(path)
            write(root, path, item["content"])
        if tree(root) == before or not any(p.startswith(("src/", "tests/")) for p in changed):
            raise Blocked(
                "No implementation/test delta in this attempt; dirty baseline is not work"
            )
        criteria = {item["id"] for item in state["contract"]["criteria"]}
        evidence = result["evidence"]
        if {item["criterion"] for item in evidence} != criteria:
            raise Blocked("Evidence does not cover exactly all SPEC criteria")
        for item in evidence:
            if not all(item[key].strip() for key in item):
                raise Blocked("Evidence contains empty input/expected/observed/test/artifact")
            artifact = safe_path(root, item["artifact"])
            if (
                not artifact.is_file()
                or not artifact.stat().st_size
                or not item["test"].startswith("tests/")
                or "::" not in item["test"]
            ):
                raise Blocked("Evidence artifact or exact pytest node is absent")
        report = self.gates.run(root, attempt / "gates")
        state["gates"] = {**asdict(report), "passed_tests": sorted(report.passed_tests)}
        state["evidence"] = evidence
        if not report.valid(tree(root)):
            if (
                report.tree_sha == tree(root)
                and len(report.commands) == 4
                and len(report.exit_codes) == 4
                and any(report.exit_codes)
                and len(report.artifacts) >= 5
                and all(
                    Path(p).is_file() and digest(Path(p).read_bytes()) == sha
                    for p, sha in report.artifacts
                )
            ):
                state.update(
                    role="sonnet",
                    review={
                        "origin": "mechanical_gates",
                        "reason": "Fix failing mandatory gates only; "
                        "do not change SPEC or weaken tests. "
                        "If failure is outside scope, TECH_BLOCK.",
                        "reviewer_result": state["review"].get("reviewer_result", state["review"]),
                    },
                )
                return
            raise Blocked("Mandatory mechanical gates failed or evidence is incomplete")
        if not {item["test"] for item in evidence} <= report.passed_tests:
            raise Blocked("Acceptance evidence cites tests that did not actually pass")
        state.update(role="astra", review={}, reason="")

    def review_ready(self, state: dict[str, Any], result: dict[str, Any]) -> None:
        if not state["gates"] or not state["evidence"]:
            raise Blocked("Reviewer cannot close without mechanical evidence")
        if state["gates"]["tree_sha"] != state["tree"]:
            raise Blocked("Review gates refer to a different implementation tree")
        for path, checksum in state["gates"]["artifacts"]:
            if not Path(path).is_file() or digest(Path(path).read_bytes()) != checksum:
                raise Blocked("Gate evidence changed before review closure")
        ids = {item["id"] for item in state["contract"]["criteria"]}
        if any(f["basis"] == "criterion" and f["reference"] not in ids for f in result["findings"]):
            raise Blocked("Review cites an uncontracted criterion")
        state["review"] = result
        if result["status"] == "REQUIRES_CHANGES":
            state["role"] = "sonnet" if result["route"] == "implementation" else "opus"
            return
        state["closed"].append(state["unit"])
        for macro, closure in CLOSURES.items():
            if state["unit"] == closure:
                state["macros_closed"].append(macro)
        remaining = [name for name in UNITS if name not in state["closed"]]
        if remaining:
            state.update(
                unit=remaining[0],
                role="opus",
                spec_sha="",
                spec_version=0,
                spec_path="",
                contract={},
                review={},
                human_resolution="",
                gates={},
                evidence=[],
                unit_attempt=0,
            )

    def prompt(self, state: dict[str, Any], root: Path, unit_text: str) -> str:
        instructions = {
            "opus": "ANALYST / ARCHITECT. Resolve pending decisions within approved authority. "
            "Return SPEC markdown, 3-7 objective criteria and exact write_paths (directory "
            "prefixes end in /). Define scope, exclusions, required tests/evidence. "
            "Do not implement. Preserve approved decisions. Escalate only HUMAN_BLOCK.",
            "sonnet": "EXECUTOR. Implement only the current versioned SPEC. Return complete UTF-8 "
            "file contents in files; the controller applies them. No direct writes. "
            "Provide tests and evidence per criterion with exact pytest node IDs, inputs, "
            "expected, observed and nonempty artifact path. Mandatory gates run after "
            "your response, before review; do not claim they ran in this model session. "
            "Do not redefine SPEC or scope. Return TECH_BLOCK for technical inability.",
            "astra": "INDEPENDENT REVIEWER. Fresh context: review SPEC vs implementation vs tests "
            "vs controller-run evidence. Find counterexamples, do not implement fixes. "
            "Only a contracted criterion or necessary supported-product invariant blocks. "
            "Other findings are debt. Route implementation defects to implementation, "
            "normative ambiguity to specification, authority decisions to HUMAN_BLOCK.",
        }
        context: dict[str, Any] = {
            "role": state["role"],
            "instructions": instructions[state["role"]],
            "unit": state["unit"],
            "unit_roadmap": unit_text,
            "spec_sha": state["spec_sha"],
            "spec_path": state["spec_path"],
            "contract": state["contract"],
            "previous_review": state["review"],
            "human_resolution": state.get("human_resolution", ""),
            "evidence": state["evidence"],
            "mechanical_gates": state["gates"],
            "repository_files": files(root),
            "constraints": "M2-M6 only. No ML training, promotion or campaigns. No remote push "
            "or merge. No shell commands. Return exactly one JSON object matching "
            "the provided schema; unused fields empty. Never edit files directly.",
            "documents": {},
        }
        if state["gates"]:
            context["gate_outputs"] = {
                path: Path(path).read_text(encoding="utf-8", errors="replace")
                for path, _ in state["gates"]["artifacts"]
            }
        sources = {
            "docs/milestone-workflow.md",
            "docs/m0-methodology-contract.md",
            "docs/m1-specification.md",
            "docs/methodology-roadmap-m2-m6.md",
        }
        if state["role"] == "astra":
            # Reviewer has no shell: supply source, scoped tests and normative evidence.
            sources.update(p for p in files(root) if p.startswith("src/") and p.endswith(".py"))
            sources.update(item["test"].split("::")[0] for item in state["evidence"])
            sources.update(item["artifact"] for item in state["evidence"])
            for path in state["contract"].get("write_paths", []):
                sources.update(
                    p
                    for p in files(root)
                    if p == path
                    or (path.endswith("/") and p.startswith(path) and p.endswith(".py"))
                )
        for path in sorted(sources):
            target = safe_path(root, path)
            if target.is_file():
                context["documents"][path] = target.read_text(encoding="utf-8")
        prompt = canonical(context)
        if len(prompt.encode()) > 8_000_000:
            raise Blocked("Context exceeds verified transport budget; no silent truncation")
        return prompt
