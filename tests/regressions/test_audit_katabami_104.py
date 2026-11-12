"""All three coupled retention decisions must use the same fixed window."""

import ast
import inspect
from datetime import timedelta
from textwrap import dedent

from datumoj.rikolto.core import state


def test_retention_window_is_fixed_and_shared_by_all_three_decisions():
    source = inspect.getsource(state)
    assert "hours=" + "72" not in source
    assert state.RETENTION_WINDOW == timedelta(hours=24)
    plan = ast.parse(inspect.getsource(state.plan_cleanup))
    mark = ast.parse(dedent(inspect.getsource(state.StateManager.mark_raw)))
    # Generation retention and raw marker age are the two comprehension filters.
    filters = [condition for node in ast.walk(plan) if isinstance(node, ast.comprehension)
               for condition in node.ifs]
    assert sum(any(isinstance(node, ast.Name) and node.id == "RETENTION_WINDOW"
                   for node in ast.walk(condition)) for condition in filters) == 2
    decisions = [node.test for node in ast.walk(mark) if isinstance(node, ast.If)]
    assert len(decisions) == 1
    assert any(isinstance(node, ast.Name) and node.id == "RETENTION_WINDOW"
               for node in ast.walk(decisions[0]))
