"""重新导出 schemas/ 的辅助脚本。

agent 环境只能跑 pytest，所以写成一个 pytest 用例：
    uv run --frozen pytest -q results/DATUMOJ-IVY-001/regen_schemas.py
有 python 时直接用：python -m datumoj.core.models schemas
"""

from pathlib import Path

from datumoj.core.models import export_schemas

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "schemas"


def test_regen_schemas():
    assert export_schemas(SCHEMAS_DIR)
