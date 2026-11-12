"""Generation retention and age-based marker protection share a fixed window."""

import ast
import inspect
from datetime import timedelta
from textwrap import dedent

from datumoj.rikolto.core import state


def test_retention_window_is_shared_and_marking_starts_at_first_publication():
    source = inspect.getsource(state)
    assert "hours=" + "72" not in source
    assert state.RETENTION_WINDOW == timedelta(hours=24)
    plan = ast.parse(inspect.getsource(state.plan_cleanup))
    mark = ast.parse(dedent(inspect.getsource(state.StateManager.mark_raw)))
    filters = [condition for node in ast.walk(plan) if isinstance(node, ast.comprehension)
               for condition in node.ifs]
    assert sum(any(isinstance(node, ast.Name) and node.id == "RETENTION_WINDOW"
                   for node in ast.walk(condition)) for condition in filters) == 2
    # Every referenced hash is marked before publication without an expiry threshold.
    loops = [node for node in ast.walk(mark) if isinstance(node, ast.For)]
    assert len(loops) == 1
    assert ast.unparse(loops[0].iter) == "hashes - before.keys()"
    assert "SELECT DISTINCT sha256 FROM snapshots WHERE sha256 IS NOT NULL" in inspect.getsource(state.StateManager.mark_raw)
    assert not any(isinstance(node, ast.If) for node in ast.walk(mark))
    assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and node.func.attr == "raw_retention" for node in ast.walk(mark))
    assert "manifest.mark_raw_optional(sha, now)" in ast.unparse(loops[0])
