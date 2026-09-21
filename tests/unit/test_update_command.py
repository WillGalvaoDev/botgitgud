"""Actual Discord command registration/checks with local doubles, never a login."""

from __future__ import annotations

import ast
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from discord.ext import commands

from botgitgud.analysis.findings import TopPriorities
from botgitgud.analysis.pipeline import AnalysisResult
from botgitgud.bot import discord_bot
from botgitgud.domain.models import RunManifest
from botgitgud.domain.specs import SpecId
from botgitgud.ingest.store import Store
from botgitgud.knowledge import KnowledgeStore, wowhead_source
from botgitgud.knowledge.spec_slugs import SPEC_SLUGS
from botgitgud.knowledge.wowhead_source import UpdateResult, UpdateStatus, format_update_results
from botgitgud.report.text import ReportHeader


def _deps(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        store=Store(tmp_path),
        client=SimpleNamespace(),
        settings=SimpleNamespace(
            data_dir=tmp_path,
            bot_stop_poll_interval_s=1.0,
        ),
    )


class _Context:
    def __init__(self, bot: commands.Bot, command: Any, author_id: int) -> None:
        self.bot = bot
        self.command = command
        self.author = SimpleNamespace(id=author_id)
        self.channel = SimpleNamespace(id=456)
        self.sent: list[str] = []

    async def send(self, content: str, **_kwargs: Any) -> None:
        self.sent.append(content)


def test_update_owner_check_refuses_before_ingestion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps = _deps(tmp_path)
    bot = discord_bot.build_bot(deps)  # type: ignore[arg-type]
    bot.owner_id = 123  # framework's own ownership model, no application_info network call
    command = bot.get_command("update")
    assert command is not None and len(command.checks) == 1
    monkeypatch.setattr(wowhead_source, "update_references", lambda _: pytest.fail("unauthorized"))

    async def invoke() -> None:
        ctx: Any = _Context(bot, command, 999)
        with pytest.raises(commands.NotOwner) as error:
            await command.can_run(ctx)
        handler: Any = bot.on_command_error
        await handler(ctx, error.value)
        assert ctx.sent == [str(error.value)]
        await bot.close()

    try:
        asyncio.run(invoke())
    finally:
        deps.store.close()


def test_update_owner_receives_one_operational_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps = _deps(tmp_path)
    bot = discord_bot.build_bot(deps)  # type: ignore[arg-type]
    bot.owner_id = 123
    command = bot.get_command("update")
    assert command is not None
    calls = []

    def update(store: KnowledgeStore) -> tuple[UpdateResult, ...]:
        calls.append(store.root)
        return (
            UpdateResult(SpecId("Warlock", "Demonology"), UpdateStatus.UPDATED),
            UpdateResult(SpecId("Mage", "Frost"), UpdateStatus.UNCHANGED, True),
            UpdateResult(SpecId("Demon Hunter", "Devourer"), UpdateStatus.UNAVAILABLE, True),
        )

    monkeypatch.setattr(wowhead_source, "update_references", update)

    async def invoke() -> None:
        ctx: Any = _Context(bot, command, 123)
        assert await command.can_run(ctx)
        callback: Any = command.callback
        await callback(ctx)
        assert len(ctx.sent) == 1
        assert "Atualizadas: 1 · Inalteradas: 1 · Falharam: 1" in ctx.sent[0]
        assert "Demon Hunter / Devourer — referencia anterior preservada." in ctx.sent[0]
        assert "spell" not in ctx.sent[0] and "http" not in ctx.sent[0]
        await bot.close()

    try:
        asyncio.run(invoke())
        assert calls == [tmp_path / "rotation_knowledge"]
    finally:
        deps.store.close()


def test_all_failures_fit_one_discord_message_and_do_not_claim_missing_reference_preserved() -> (
    None
):
    results = tuple(
        UpdateResult(SpecId(slug.class_name, slug.spec_name), UpdateStatus.FAILED)
        for slug in SPEC_SLUGS.values()
    )
    message = format_update_results(results)
    assert len(message) <= 2000
    assert "Falharam: 26" in message
    assert "anterior preservada" not in message
    assert "referencia indisponivel" in message


def test_analisar_cannot_call_rotation_source_or_refresh() -> None:
    tree = ast.parse(Path(discord_bot.__file__).read_text(encoding="utf-8"))
    analyze = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "cmd_analisar"
    )
    update = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "cmd_update"
    )
    assert any(
        isinstance(node, ast.ImportFrom) and node.module == "botgitgud.knowledge.wowhead_source"
        for node in ast.walk(update)
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "run_in_executor_with_context"
        for node in ast.walk(analyze)
    )
    for node in ast.walk(analyze):
        if isinstance(node, ast.Name):
            assert node.id not in {"update_references", "WowheadSource", "cmd_update", "httpx"}
        if isinstance(node, ast.ImportFrom):
            assert node.module != "botgitgud.knowledge.wowhead_source"
    # No top-level adapter import can couple the normal command to ingestion.
    assert not any(
        isinstance(node, ast.ImportFrom) and node.module == "botgitgud.knowledge.wowhead_source"
        for node in tree.body
    )


def test_analisar_completes_without_rotation_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps = _deps(tmp_path)
    bot = discord_bot.build_bot(deps)  # type: ignore[arg-type]
    command = bot.get_command("analisar")
    assert command is not None
    assert not (tmp_path / "rotation_knowledge").exists()
    result = AnalysisResult(
        header=ReportHeader("Zarad", "Boss", "Warlock", "Demonology", 20, 300.0, 360.0),
        comparisons=(),
        manifest=RunManifest(
            cohort_id="synthetic-cohort",
            code_version="test",
            generated_at=datetime(2026, 9, 9, tzinfo=UTC),
            n_members=20,
            wcl_partition=None,
            settings_hash="synthetic-settings",
        ),
        setup_analysis=None,  # type: ignore[arg-type]
        benchmark_target=None,
        benchmark_policy=None,
        performance=None,
        dps_gap=None,
        top_actions=TopPriorities(),
    )
    monkeypatch.setattr(discord_bot, "run_analysis", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(
        wowhead_source,
        "update_references",
        lambda _: pytest.fail("analysis must stay local"),
    )

    async def invoke() -> None:
        ctx = _Context(bot, command, 123)
        callback: Any = command.callback
        await callback(ctx, "Zarad", "ABCDEFGHIJKLMNOP?fight=1")
        assert len(ctx.sent) == 1 and "prioridade de coaching" in ctx.sent[0]
        assert "http://" not in ctx.sent[0] and "https://" not in ctx.sent[0]
        assert not (tmp_path / "rotation_knowledge").exists()
        await bot.close()

    try:
        asyncio.run(invoke())
    finally:
        deps.store.close()
