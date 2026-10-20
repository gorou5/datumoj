---
id: DATUMOJ-IVY-302
title: 地区码表：全国地方公共団体コード
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-IVY-003"]
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/reference/**", "tests/reference/**", "results/DATUMOJ-IVY-302/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: /opt/homebrew/bin/uv run --frozen pytest -q tests/reference/
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-302/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
全项目统一用总务省的地方公共团体代码表示地区，名称和各种代码写法可以互转。

# 背景
开发线：C 统计线。旧项目没有地区码表，这次新建。代码表原始文件由主对话放在 `tests/fixtures/area_codes/` 和 `src/datumoj/reference/data/`。

计划见 `docs/PLAN.md`，任务总表、开发线和跨任务约定见 `docs/TASKS.md`。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。不要 import 其他开发线的实现模块（集成任务除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-IVY-302/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。

# 要求
1. 6 位（含校验位）和 5 位互转，校验位按总务省规则实现。
2. `lookup(name_or_code)`：支持“東京都”“東京”“13”“13000”“131016”；同名市（如“府中市”）不带都道府县时返回多个候选，不猜。
3. 47 个都道府县代码固定；全国 `00000`。
4. `normalize_area_label(s)`：全角、空格、都道府县名与市区町村名连写。
5. 测试覆盖校验位、同名市、简称、无效代码。

# 不做
- 不处理市町村合并沿革，不做坐标。
