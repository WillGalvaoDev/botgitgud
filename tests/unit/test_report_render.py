from __future__ import annotations

import ast
import inspect
import pathlib
from types import FunctionType

import botgitgud.bot.discord_bot as discord_module
import botgitgud.bot.worker as worker_module
import botgitgud.report.render as render_module
from botgitgud.report.contract import ReportContract
from botgitgud.report.render import render_text_from_contract


def _called_names(source: FunctionType | ast.AST) -> set[str]:
    tree = ast.parse(inspect.getsource(source)) if isinstance(source, FunctionType) else source
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _imported_names(module: object) -> set[str]:
    tree = ast.parse(inspect.getsource(module))  # type: ignore[arg-type]
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(a.asname or a.name for a in node.names)
    return names


def test_render_module_only_exposes_surviving_contract_and_text_paths() -> None:
    assert hasattr(render_module, "contract_for")
    assert hasattr(render_module, "render_text_from_contract")
    assert hasattr(render_module, "render_text_report")
    assert not hasattr(render_module, "render_analysis")
    assert not hasattr(render_module, "RenderedReport")


def test_discord_and_worker_construct_the_canonical_contract() -> None:
    """RB-6/§11: o fato autoritativo é a CHAMADA de `contract_for` dentro do
    caminho real, não a presença do texto em algum lugar do arquivo — uma
    função poderia mencionar o nome num comentário e passar.
    """
    tree = ast.parse(inspect.getsource(discord_module))
    handler = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "cmd_analisar"
    )
    assert "contract_for" in _called_names(handler)

    worker_tree = ast.parse(inspect.getsource(worker_module))
    run_claimed_job = next(
        n
        for n in ast.walk(worker_tree)
        if isinstance(n, ast.FunctionDef) and n.name == "run_claimed_job"
    )
    assert "contract_for" in _called_names(run_claimed_job)


def test_cli_text_path_still_constructs_contract() -> None:
    assert "contract_for" in _called_names(render_module.render_text_report)


def test_no_production_module_imports_a_renderer_directly() -> None:
    """A prova de "nenhum bypass": fora de `report/`, só `report/render.py`
    importa os renderizadores de `report/text.py` que sobrevivem a M32
    (`render_report`, `render_header_and_top3`).
    """
    root = pathlib.Path(inspect.getfile(discord_module)).parents[1]
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if path.parent.name == "report" or "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module is None:
                continue
            if node.module.startswith("botgitgud.report.render"):
                continue
            for alias in node.names:
                if alias.name in {"render_report", "render_header_and_top3"}:
                    offenders.append(f"{path.name}:{alias.name}")
    assert offenders == [], offenders


def test_render_module_touches_no_network_or_discord() -> None:
    imported = _imported_names(render_module)
    tree = ast.parse(inspect.getsource(render_module))
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not any(m.startswith(("botgitgud.wcl", "botgitgud.ingest")) for m in modules), modules
    assert "discord" not in imported


def test_renderers_consume_contract_fields_only() -> None:
    """O único adaptador que sobrevive (`render_text_from_contract`) recebe
    um `ReportContract` e nada mais — nunca um `AnalysisResult` bruto.
    """
    params = list(inspect.signature(render_text_from_contract).parameters.values())
    assert len(params) == 1
    assert params[0].annotation in ("ReportContract", ReportContract)
