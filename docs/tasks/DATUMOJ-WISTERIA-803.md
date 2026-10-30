---
id: DATUMOJ-WISTERIA-803
title: Eltiro 匹配：确定性匹配与向量召回、匹配记录持久化、人工确认队列
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-WISTERIA-801", "DATUMOJ-WISTERIA-802"]
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 2, api: [], port: 0}
scope: ["src/datumoj/eltiro/matching.py", "src/datumoj/eltiro/match_store.py", "src/datumoj/eltiro/cli.py", "docs/eltiro/matching.md", "tests/eltiro/test_matching.py", "tests/eltiro/test_match_store.py", "tests/eltiro/test_cli.py", "tests/regressions/test_audit_wisteria_803.py", "results/DATUMOJ-WISTERIA-803/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "docs/PLAN.md", "docs/plans/**", "docs/adr/**", "sources/**", "schemas/**", "eval/**", "src/datumoj/rikolto/**", "src/datumoj/eltiro/models.py", "src/datumoj/eltiro/contracts.py", "src/datumoj/eltiro/intake.py", "src/datumoj/eltiro/registry_store.py", "src/datumoj/eltiro/profiles.py", "src/datumoj/eltiro/profile_store.py", "src/datumoj/eltiro/eval.py", "src/datumoj/eltiro/routing.py", "src/datumoj/eltiro/model_cache.py", "src/datumoj/eltiro/providers.py", "src/datumoj/eltiro/prompts.py"]
protected: ["tests/fixtures/**", "src/datumoj/eltiro/testing/**", "docs/plans/0.4-wisteria-samples/**"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_wisteria_803.py
    - cmd: make check T=tests/eltiro
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-WISTERIA-803/summary.md 完成说明（含置信度的算法、自动绑定的条件、用合成标准答案跑 eval.score_matches 的结果）"]
  review: {by: other_family, checklist: ["候选是否只来自使用方能用的来源", "自动绑定是否只在确定性档、官方代码精确命中、三项口径都为 same 时发生", "向量档是否从不自动绑定", "口径、单位、调查不同的序列是否分开成候选、没有合并", "反例命中的序列是否不被自动绑定", "人确认和否决是否在新代次里沿用", "每条匹配记录的依据是否来自 Rikolto 只读视图、没有臆造快照哈希", "持久化匹配库是否通过 001 的契约测试套件", "是否只读 Rikolto、不写它的库和向量索引", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
把在用需求里的每个指标匹配到序列画像上：先用确定性方法（官方代码、概念编号、关键词），再用向量召回。每条候选写成匹配记录，记下理由、置信度和依据。匹配记录持久化。不能自动绑定的候选进人工确认队列；人确认或否决的结果在后续代次里沿用。

# 背景
开发线：Eltiro。契约见 WISTERIA-001：`DemandSpec`、`SeriesProfile`、`MatchRecord`、`Compatibility`、`MatchStore` 和它的契约测试套件。设计见 `docs/plans/0.4-wisteria.md` 第 6.3 节和 ADR 0006。输入来自已完成的任务：
- 801：需求登记、使用方配置 `consumers.yaml`；
- 802：画像库 `SQLiteProfileStore`；
- 701：评测打分 `eval.score_matches`。

模型判断档（第三档）不在本任务，由 807 做。增量重匹配和提醒由 808 做。

本版本在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- 本任务新建 `eltiro/matching.py`（匹配）和 `eltiro/match_store.py`（持久化），并在 `eltiro/cli.py` 里加确认队列的命令。
- Eltiro 的模型、契约和测试工具只读；801、802、701 的文件不改；Rikolto 的代码一律不改。需要改契约或模型时，写进 `summary.md` 并停下来问。
- 同时在跑的 806 只用 `routing.py`、`model_cache.py`、`providers.py`、`prompts.py`，不要碰。
- 存储用标准库 `sqlite3`。向量可以用已装的 `lancedb` 或 sqlite，不加依赖。
- 回归测试写在 `tests/regressions/test_audit_wisteria_803.py`；调度器不接受 `skip` / `xfail`。
- 测试全部离线，临时文件一律放 `tmp_path`。嵌入用 Rikolto 测试用的确定性 `HashEmbedder`，不连 Ollama。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、账号 ID、真实主机名或内网地址、本机绝对路径。

# 要求
1. **输入**：匹配函数接收这些参数：
   - 一份已登记的需求；
   - 调用方给的代次号；
   - 该代次的画像（`ProfileStore`）；
   - 这个使用方的配置；
   - 取依据用的 Rikolto 只读视图；
   - 可选的 `Embedder`。
   
   怎样取得已发布代次由调用方负责，本任务不读状态存储。候选只能来自这个使用方 `allowed_sources` 里的来源。`preferred_sources` 只用来排序加分，不用来过滤。许可等级不影响匹配：`no_outflow` 的来源也可以参与匹配，输出时的限制由 805 处理。
2. **确定性档**：
   - **官方代码**：从指标的概念说明和正例里提取代码形态的记号，和画像的 `indicator_code`、`concept_ids` 精确比较。
   - **关键词**：用指标的概念说明和正例，对画像各语言的名称和说明做关键词匹配。
   - **反例**：画像命中反例的，不能自动绑定。可以作为候选，但理由里写明命中了哪条反例。
   
   置信度用固定公式算出，写进 `docs/eltiro/matching.md`。同样的输入，结果必须完全相同。
3. **向量档**：
   - 用传入的 `Embedder` 嵌入两侧文本：画像各语言的名称和说明，指标的概念说明和正例。每个指标取前 k 个，k 有上限。
   - 向量存在 `<data>/eltiro/` 下，按代次和嵌入模型（名称、维度）区分；嵌入模型不同就拒绝混用。不写 Rikolto 的向量索引。
   - 只允许本地嵌入：`Embedder` 由调用方传入，本任务不接云端嵌入。
   - 向量档的候选**一律不自动绑定**。
   - 不传 `Embedder` 时跳过这一档，并在结果里注明。
4. **每档可关闭**：确定性档和向量档各有开关。两档都关闭时报错，不输出空结果。
5. **口径与自动绑定**：
   - `Compatibility` 只按证据填，判断不了就是 `unknown`。
   - `unit`：画像单位和需求的 `unit` 去掉首尾空白、忽略大小写后相等，为 `same`；两边都写了但不等，为 `different`。
   - `methodology`、`survey`：只有官方代码精确命中时才为 `same`，理由是代码本身限定了口径和调查。
   - `auto_bind=true` 同时要求：确定性档、官方代码精确命中、三项都为 `same`、没有命中反例、没被人否决。其余候选一律 `pending`，进人工确认队列。
   - 口径、单位、调查不同的序列各自成为候选，不合并；需求里 `keep_separate_by` 列出的维度也一样。
6. **依据**：每条匹配记录至少一条 `MatchEvidence`。`source_id`、`snapshot_sha256`、`locator` 必须来自同一代次的 Rikolto 只读视图（观测值、目录或文档块）。取不到依据的序列不能成为候选，并计入结果里的"缺依据"清单。不臆造哈希。
7. **持久化**：
   - `match_store.py` 用 sqlite 实现 `MatchStore`，存在 `<data>/eltiro/matches.sqlite`，通过 001 的契约测试套件。
   - 写入一次匹配结果（一份需求的一个代次）是原子的。
   - 对同一代次重跑，结果和第一次相同。
8. **人工确认在新代次沿用**：对新代次匹配时，旧代次里人对同一个 `(demand_id, demand_version, indicator_key, series)` 的决定照样生效：
   - 人确认过的，新代次里仍是 `confirmed`，排在前面；
   - 人否决过的，新代次里仍是 `rejected`，不再作为推荐。
   - 只在同一需求版本内沿用（shortcut：不跨版本沿用，因为新版本的概念说明可能变了；在代码里写一行注释说明）。
   - 序列在新代次里已不存在的，不沿用，也不报错。
9. **命令行**（`eltiro`）：
   - `eltiro matches queue [--consumer <id>] [--demand <id>]`：列出待确认的候选。
   - `eltiro matches confirm|reject <demand_id> --version N --indicator <key> --series <序列身份 JSON> --generation G`。
   - 错误信息不带需求原文。
   - 启动匹配的命令本任务不加：它需要固定代次的 Rikolto 读取，由 804 接上；本任务提供 Python 接口。
10. **评测接线**：用合成的标准答案调用 701 的 `eval.score_matches`，在测试里断言完美、漏配、错配三种情况都算对。结果写进 `summary.md`。真实的标准答案都是 `pending_h2`，本任务不要求达标。
11. **回归测试** `test_audit_wisteria_803.py` 覆盖：
    - 官方代码精确命中、三项口径都为 same 时自动绑定；
    - 单位不同、口径未知、命中反例、向量档时都不自动绑定；
    - 同名但调查不同的两个序列分成两条候选；
    - 使用方不能用的来源不出现在候选里；
    - 依据取自 Rikolto 视图，取不到依据的进"缺依据"清单；
    - 人确认和否决在新代次沿用，被否决的不再推荐；
    - 同一代次重跑结果相同；
    - 两档都关闭时报错；
    - 嵌入模型不同就拒绝混用；
    - 生成过程不调用 Rikolto 的任何写接口（用记录调用的假实现断言）；
    - sqlite 匹配库通过契约测试套件。
12. `make check T=tests` 全部通过。

# 不做
- 不做模型判断档（807），不调用任何生成模型。
- 不做增量重匹配和提醒（808），也不做缺口报告（809）。
- 不做执行和输出（804、805），不加启动匹配的命令。
- 不改 Rikolto 的代码，不改 Eltiro 的模型和契约。
