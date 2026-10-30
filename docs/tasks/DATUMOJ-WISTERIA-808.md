---
id: DATUMOJ-WISTERIA-808
title: Eltiro 增量重匹配与提醒：新代次的变化序列反向匹配在用需求，停更、口径变化、新候选、缺口转为可得；收紧官方代码的判断
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-WISTERIA-803"]
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 2, api: [], port: 0}
scope: ["src/datumoj/eltiro/rematch.py", "src/datumoj/eltiro/alerts.py", "src/datumoj/eltiro/matching.py", "docs/eltiro/rematch.md", "docs/eltiro/matching.md", "tests/eltiro/test_rematch.py", "tests/eltiro/test_alerts.py", "tests/eltiro/test_matching.py", "tests/regressions/test_audit_wisteria_808.py", "results/DATUMOJ-WISTERIA-808/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "docs/PLAN.md", "docs/plans/**", "docs/adr/**", "sources/**", "schemas/**", "eval/**", "src/datumoj/rikolto/**", "src/datumoj/eltiro/models.py", "src/datumoj/eltiro/contracts.py", "src/datumoj/eltiro/intake.py", "src/datumoj/eltiro/registry_store.py", "src/datumoj/eltiro/cli.py", "src/datumoj/eltiro/profiles.py", "src/datumoj/eltiro/profile_store.py", "src/datumoj/eltiro/match_store.py", "src/datumoj/eltiro/eval.py", "src/datumoj/eltiro/routing.py", "src/datumoj/eltiro/model_cache.py", "src/datumoj/eltiro/providers.py", "src/datumoj/eltiro/prompts.py", "src/datumoj/eltiro/executor.py", "src/datumoj/eltiro/operations.py", "src/datumoj/eltiro/areas.py", "src/datumoj/eltiro/runs.py", "src/datumoj/eltiro/gaps.py", "tests/regressions/test_audit_wisteria_803.py"]
protected: ["tests/fixtures/**", "src/datumoj/eltiro/testing/**", "docs/plans/0.4-wisteria-samples/**"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_wisteria_808.py
    - cmd: make check T=tests/regressions/test_audit_wisteria_803.py
    - cmd: make check T=tests/eltiro
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-WISTERIA-808/summary.md 完成说明（含反向索引怎样建、四种提醒的判定规则、增量结果和全量重匹配的对比）"]
  review: {by: other_family, checklist: ["是否只对可能受影响的在用需求重匹配", "增量结果是否和全量重匹配一致", "四种提醒的判定是否只按画像和匹配记录里的证据", "提醒是否按需求的 notify_on 过滤、不重复", "提醒是否不含需求原文、不提及其他使用方", "通用全大写缩写是否不再被当成官方代码", "803 的回归测试是否原样通过", "是否只读 Rikolto", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
每出一个新代次，Eltiro 自动做两件事：
- 找出变化的序列，反过来只对可能受影响的在用需求重新匹配；
- 对已绑定的序列检查四种情况并生成提醒：停更、口径或单位变化、出现更好的候选、需求要的数据从"缺"变成"有"。

同时修掉 803 审查里指出的几处匹配问题。

# 背景
开发线：Eltiro。设计见 `docs/plans/0.4-wisteria.md` 第 6.4 节。需求里 `run.notify_on` 列出使用方要的提醒种类：`binding_stale`、`definition_changed`、`new_candidate`、`gap_filled`。已完成的实现：
- 802：按代次存画像，`SQLiteProfileStore`；
- 803：`match_demand` 和 `SQLiteMatchStore`。803 只在同一需求版本内沿用人的决定。

803 的异源审查指出几处不阻塞、但应修的问题，本任务一并修（改 `matching.py`）：
- `_codes` 把 `GDP`、`CPI` 这类两个字母以上的全大写词也当官方代码。来源的指标代码恰好是这种缩写时，会被当作精确命中而自动绑定。
- 只开向量档、却没传 `Embedder` 时返回空结果、不报错，实际上没有任何一档在跑。
- 确定性档关闭时，靠沿用人工决定生成的记录 `tier` 仍标为 `deterministic`。

本版本在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- 本任务新建 `rematch.py`（增量重匹配）和 `alerts.py`（提醒），并修改 `matching.py` 的上面三处；不改 `match_store.py`。
- `cli.py` 这一轮归 804 改，本任务**不加命令行**，只提供 Python 接口；命令行以后接上。
- 同时在跑的 804 用 `executor.py`、`operations.py`、`areas.py`、`runs.py`、`cli.py`，809 用 `gaps.py`，都不要碰。
- Eltiro 的模型、契约和测试工具只读；Rikolto 的代码一律不改。需要改契约或模型时，写进 `summary.md` 并停下来问。
- `test_audit_wisteria_803.py` 不许改，必须原样通过。
- 存储用标准库 `sqlite3`，不加依赖。
- 回归测试写在 `tests/regressions/test_audit_wisteria_808.py`；调度器不接受 `skip` / `xfail`。
- 测试全部离线，临时文件一律放 `tmp_path`；嵌入用 `HashEmbedder`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、账号 ID、真实主机名或内网地址、本机绝对路径。

# 要求
1. **变化序列**：比较新代次和上一代次的画像，得出新增、修改、删除的序列（按完整序列身份和画像内容哈希）。怎样取得已发布代次由调用方负责。
2. **反向索引**：
   - 从在用需求的指标建反向索引：官方代码、关键词、偏好来源。
   - 只把和变化序列有交集的需求挑出来重匹配。
   - 已绑定（人确认或自动绑定）的序列有变化时，对应需求一定要重匹配。
   - 用了向量档的需求，变化序列的向量和指标向量相似度过线的，也要挑出来。
3. **重匹配**：
   - 对挑出的需求调用 803 的 `match_demand`。
   - 结果必须和对同一代次全量重匹配完全一致（测试断言）。
   - 没被挑中的需求，把上一代次的匹配结果原样带到新代次，不重新计算。
4. **四种提醒**，只按画像和匹配记录里的证据判断：
   - `binding_stale`：已绑定序列的 `time_end` 超过它的频率一个周期仍没前进。判定日期由调用方传入，不读系统时钟。
   - `definition_changed`：已绑定序列在新旧代次之间单位、倍率、口径说明、调查任一项变了，并写明变了哪一项。
   - `new_candidate`：出现置信度比现有绑定更高的候选，或者原来没有绑定的指标出现了可自动绑定的候选。
   - `gap_filled`：指标原来没有候选，或者只有 `catalog_only`、`document_only` 的候选，现在有了 `observations` 的候选。
   
   提醒的规则：
   - 只生成需求 `notify_on` 里列出的种类。
   - 同一提醒（需求、指标、序列、种类、代次）不重复生成。
   - 提醒存进 `<data>/eltiro/alerts.sqlite`，可以确认（ack）。
   - 提醒内容不含需求原文，不提及其他使用方。
   - 不主动推送，不写使用方发件目录（计划第 5 节）。
5. **Python 接口**：
   - `refresh(new_generation, previous_generation, …)`：对全部在用需求做增量重匹配并生成提醒，返回计数。
   - `list_alerts(consumer=…, demand=…, include_acked=…)`。
   - `ack_alert(alert_id)`。
6. **修 803 的三处**：
   - 只把带分隔符（`.`、`_`、`:`、`/`、`-`）或数字的记号当官方代码，通用的全大写缩写不再算。
   - 开了向量档但没传 `Embedder`、又关了确定性档时报错。
   - 靠沿用人工决定生成的记录，`tier` 按实际产生它的档位标注；没有档位产生它时不生成记录。
   - `docs/eltiro/matching.md` 相应更新。
7. **回归测试** `test_audit_wisteria_808.py` 覆盖：
   - 只有受影响的需求被重匹配：用记录调用的假实现断言没被挑中的需求没有调用 `match_demand`；
   - 增量结果和全量重匹配逐条一致；
   - 四种提醒各自在该出现时出现、不该出现时不出现；
   - `notify_on` 过滤生效；
   - 同一提醒不重复；
   - 提醒里没有需求原文；
   - 删除的序列不残留绑定；
   - `GDP`、`CPI` 这类缩写和来源代码相同时不自动绑定，`NY.GDP.MKTP.CD` 这类结构化代码仍会；
   - 只开向量档、不传 `Embedder` 时报错；
   - 不调用 Rikolto 的任何写接口。
8. `make check T=tests` 全部通过，包括 `test_audit_wisteria_803.py`。

# 不做
- 不加命令行，不做管理页面和每周报告（601）。
- 不重新执行需求、不产出输出（804、805）。
- 不做缺口报告（809），不调用模型。
- 不改 Rikolto 的代码，不改 Eltiro 的模型和契约。
