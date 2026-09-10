from __future__ import annotations

import ast
import inspect

import botgitgud.bot.delivery as delivery_module


def test_delivery_surface_has_no_file_or_url_infrastructure() -> None:
    source = inspect.getsource(delivery_module)
    tree = ast.parse(source)
    function_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "deliver_completed_report" in function_names
    assert "deliver_existing_report" not in function_names
    assert "deliver_report_link" not in function_names
    assert "http://" not in source and "https://" not in source


def test_completed_delivery_signature_has_no_retired_config() -> None:
    parameters = inspect.signature(delivery_module.deliver_completed_report).parameters
    assert tuple(parameters) == ("channel", "contract", "artifact_id", "context")
