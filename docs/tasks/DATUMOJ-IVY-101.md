---
id: DATUMOJ-IVY-101
title: raw 存储：按内容寻址
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-IVY-002"]
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/core/rawstore.py", "tests/core/test_rawstore.py", "results/DATUMOJ-IVY-101/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_rawstore.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-101/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
原始字节按 sha256 存进 `data/raw/<前两位>/<sha256>`，同样内容只存一份，可校验。

# 背景
开发线：A 抓取基础。PLAN 4.1：派生数据都能从 raw 重新生成，所以 raw 必须不可变、可校验。

计划见 `docs/PLAN.md`，任务总表、开发线和跨任务约定见 `docs/TASKS.md`。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。不要 import 其他开发线的实现模块（集成任务除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-IVY-101/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。

# 要求
1. 实现 `RawStore` 协议：`put`（bytes 或流）、`get`、`open`、`exists`、`verify`、`path_for`。
2. 先写临时文件再原子 rename；重复写入相同内容不报错。大文件流式写入（测试用 >10MB 生成数据）。
3. 存好的文件设为只读；`verify` 发现内容和哈希不一致时报 `CorruptedBlob`。
4. 根目录来自参数或 `DATUMOJ_DATA_DIR`（默认 `./data`）。
5. 继承 `RawStoreSuite` 跑通合格测试，再补本实现特有的测试。

# 不做
- 不管 manifest 和 HTTP。
