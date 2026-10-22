#!/usr/bin/env python3
"""最小可行校验：examples/ 下的 JSON 必须符合 schemas/ 的 JSON Schema。

用法: python tests/validate_schemas.py
依赖: pip install jsonschema
"""
import json
import sys
from pathlib import Path

try:
    import jsonschema
except ImportError:
    print("ERROR: 需要 pip install jsonschema")
    sys.exit(2)

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = {
    "data-source": ROOT / "schemas" / "registry" / "data-source.schema.json",
    "module": ROOT / "schemas" / "registry" / "module.schema.json",
}
# 目录前缀 -> schema 名
ROUTING = {
    "examples/data-sources": "data-source",
    "catalog": "data-source",
    "connectors": "module",
    "skills": "module",
    "agents": "module",
    "mcp": "module",
    "workers": "module",
}

failures = []
checked = 0

for schema_name, schema_path in SCHEMAS.items():
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    print(f"schema OK: {schema_path.relative_to(ROOT)}")

for prefix, schema_name in ROUTING.items():
    d = ROOT / prefix
    if not d.is_dir():
        continue
    schema = json.loads(SCHEMAS[schema_name].read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    for f in sorted(d.rglob("*.json")):
        checked += 1
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            failures.append(f"{f}: JSON 解析失败: {e}")
            continue
        errors = list(validator.iter_errors(doc))
        if errors:
            failures.append(f"{f}: " + "; ".join(e.message for e in errors[:3]))
        else:
            print(f"valid: {f.relative_to(ROOT)} ({schema_name})")

print(f"共校验 {checked} 个描述文件")
if failures:
    print("失败:")
    for x in failures:
        print(" -", x)
    sys.exit(1)
print("全部通过")
