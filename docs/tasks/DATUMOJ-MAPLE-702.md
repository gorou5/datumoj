---
id: DATUMOJ-MAPLE-702
title: 回答规则：跨国比较口径、多语言出处、数据截止时间写代次时间
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-MAPLE-106"]
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["skills/datumoj/SKILL.md", "tests/test_skill_rules.py", "results/DATUMOJ-MAPLE-702/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/eval/test_questions_behavior.py", "tests/test_mcp_server.py"]
acceptance:
  automated:
    - cmd: make check T=tests/test_skill_rules.py
    - cmd: make check T=tests/eval/test_questions_behavior.py
    - cmd: make check T=tests/test_mcp_server.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-702/summary.md 完成说明"]
  review: {by: human, checklist: ["规则是否与工具的真实参数和输出一致", "是否保留已有规则", "跨国比较是否禁止自行换算", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
回答跨国比较、多语言资料和数据时效的问题时规则清楚：数值只从 `query_sql` 取，单位、年份、货币原样说明不自行换算；引用外语原文时给原文和译文；回答写明数据截止到哪个代次的生成时间。

# 背景
开发线：G 评测线。计划 6.6、6.4 节和第 11 节（跨国比较的规则也是以后多源核对的基础）。规则写在 `skills/datumoj/SKILL.md`，`tests/test_skill_rules.py` 检查关键句（TACHIBANA-702 的写法）。106 已让 MCP 的 `version` 工具报出代次号和生成时间、向量覆盖。欧美来源本批还没入库，规则先写，评测题由后续的 701 做。

计划见 `docs/plans/0.3-maple.md`（第 6.1–6.5 节是云端运行的设计，总计划 `docs/PLAN.md`）。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。检查点 m0（证据正确性）已完成。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。存储边界见 `docs/adr/0002-network-storage.md`，证据身份见 `docs/adr/0004-evidence-identity.md`，状态代次见 `docs/adr/0005-state-generations.md`（由 MAPLE-001 写出）。除契约外，只能 import 已有模块和本任务 `depends_on` 里已合并任务的模块。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 / R2 一律用 moto（锁定的 moto 5.2.4 支持 `IfNoneMatch` / `IfMatch` 条件写入）。不连真实 R2。
- R2 凭证只从环境变量 `R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY` 读，endpoint、bucket 沿用 `core/storage.py` 的 `StorageConfig`（`R2_ENDPOINT`、`DATUMOJ_S3_BUCKET`、`DATUMOJ_S3_PREFIX`）。**仓库和 Actions 日志都是公开的**：命令输出、日志、异常信息里不出现凭证、endpoint、bucket 名和原始数据内容，只写计数、来源 ID、代次号和对象键的相对部分。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-702/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. 加“跨国比较”规则：数值只从 `query_sql` 取并带逐行出处；不同来源、不同口径（单位、基准年、名义 / 实际、货币）的数值不相加、不直接比大小，先说明口径差异；不自行换算货币和口径；国家和地区名称以数据里的编码为准。
2. 加“多语言出处”规则：引用非提问语言的原文时，先给原文片段，再给标明为译文的翻译；不把译文当原文引用；检索可以用原语言的关键词再查一次。
3. 加“数据时效”规则：回答里写明数据截止到 `version` 工具报出的代次生成时间；向量覆盖不全时说明部分资料只能按关键词检索到；本机自建数据时说明不是 R2 上的代次。
4. 保持 IVY、TACHIBANA 已有规则不变（不删、不弱化）。`tests/test_skill_rules.py` 加上新规则的关键句检查，原有检查不改；读取 SKILL.md 的现有测试（`tests/eval/test_questions_behavior.py`、`tests/test_mcp_server.py`）必须仍然通过。

# 不做
- 不改 MCP 工具和代码。
- 不写评测题（701）。
