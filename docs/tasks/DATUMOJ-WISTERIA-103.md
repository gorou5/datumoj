---
id: DATUMOJ-WISTERIA-103
title: lint 清零并进 CI：ruff 规则写死在 pyproject，机械修正，CI 加 lint 步骤
project: DATUMOJ
status: done
priority: 2
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/**", "tests/**", "pyproject.toml", ".github/workflows/ci.yml", "results/DATUMOJ-WISTERIA-103/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/**", "sources/**", "schemas/**", "eval/**", ".github/workflows/cloud-run.yml", "deploy/**"]
protected: ["tests/fixtures/**"]
acceptance:
  automated:
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "make lint 输出（0 条）", "变更文件清单", "results/DATUMOJ-WISTERIA-103/summary.md 完成说明（含选定的规则集、每条 ignore 的理由、手工修改的位置清单）"]
  review: {by: other_family, checklist: ["规则集是否写死在 pyproject、不依赖本机或 ruff 版本缺省", "改动是否只是机械修正、没有改变行为", "BLE001 是否只做了按文件的临时 ignore、留给 104", "schema 导出是否逐字节不变", "回归测试文件是否只有 import 排序等机械改动、断言一条没动", "CI 是否加了 lint 步骤且用锁定的 ruff", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
2026-10-29 外部审查发现：`make lint` 报 408 条，CI 不跑 lint。规则集也没写在 `pyproject.toml` 里，结果随 ruff 版本缺省而变。本任务把规则写死、清零，并让 CI 跑 lint，以后的任务不再积累。

# 背景
- `pyproject.toml` 的 `[tool.ruff]` 只有 `line-length = 120`、`target-version = "py312"`。ruff 版本由 `uv.lock` 锁定（0.17.1）。
- 当前分布：I001 110、BLE001 49、C408 38、F401 38、FURB157 27、RUF059 26、FURB167 22、UP017 19、TRY004 19、B008 14，其余几十条零散。约 232 条可自动修。
- `.github/workflows/ci.yml` 的测试 job 用 `uv sync --frozen --python 3.12` 和 `uv run --frozen pytest -q`。

本版本在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- 本任务改动面很大，运行期间不能有其他任务同时在跑。
- 只做机械修正，不改行为、不改公开接口、不重构。
- 回归测试（`tests/regressions/**`）只允许 import 排序、删除未使用的 import 这类机械改动，断言一条不许动。
- 契约文件 `core/models.py`、`core/contracts.py`、`eltiro/models.py`、`eltiro/contracts.py` 只允许机械修正；改完 schema 导出必须和 `schemas/` 逐字节一致。
- 仓库是公开的：不写账号、密钥、本机绝对路径。

# 要求
1. **规则写死**：在 `pyproject.toml` 加 `[tool.ruff.lint]`，明确 `select`（以现在实际生效的规则为基础）和 `ignore`：
   - `TRY004` 全局 ignore：契约约定类型错误抛 `ValueError`；
   - `B008` 只对 typer 命令行文件按文件 ignore；
   - `BLE001` 暂时按文件 ignore，注释写明"由 WISTERIA-104 逐处处理"；
   - 其他 ignore 每条都要一行理由。
2. **清零**：先 `ruff check --fix`（不用 `--unsafe-fixes`），剩下的手工改。手工改的位置列进 `summary.md`。
3. **CI**：`ci.yml` 的测试 job 在跑测试前加一步 `uv run --frozen ruff check src tests`；不改其他 job，不碰 `cloud-run.yml`。
4. `make lint` 输出 0 条，`make check T=tests` 全部通过，schema 导出逐字节一致。

# 不做
- 不处理 blind-except 的写法（104），不拆复杂函数，不改格式化（不跑 `ruff format`）。
- 不改依赖，不改 `uv.lock`。
