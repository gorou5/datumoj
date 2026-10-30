---
id: DATUMOJ-WISTERIA-810
title: Eltiro 需求页面转草稿：解析 verify / cases 页面的案例、记录更新，生成需求定义草稿；草稿不登记，人改好后走现有提交
project: DATUMOJ
status: done
priority: 2
depends_on: ["DATUMOJ-WISTERIA-801", "DATUMOJ-WISTERIA-806"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/eltiro/page_cases.py", "src/datumoj/eltiro/drafts.py", "docs/eltiro/drafts.md", "tests/eltiro/test_page_cases.py", "tests/eltiro/test_drafts.py", "tests/regressions/test_audit_wisteria_810.py", "results/DATUMOJ-WISTERIA-810/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "docs/PLAN.md", "docs/plans/**", "docs/adr/**", "sources/**", "schemas/**", "eval/**", "src/datumoj/rikolto/**", "src/datumoj/eltiro/models.py", "src/datumoj/eltiro/contracts.py", "src/datumoj/eltiro/intake.py", "src/datumoj/eltiro/registry_store.py", "src/datumoj/eltiro/cli.py", "src/datumoj/eltiro/profiles.py", "src/datumoj/eltiro/profile_store.py", "src/datumoj/eltiro/matching.py", "src/datumoj/eltiro/match_store.py", "src/datumoj/eltiro/eval.py", "src/datumoj/eltiro/routing.py", "src/datumoj/eltiro/model_cache.py", "src/datumoj/eltiro/providers.py", "src/datumoj/eltiro/prompts.py", "src/datumoj/eltiro/executor.py", "src/datumoj/eltiro/operations.py", "src/datumoj/eltiro/areas.py", "src/datumoj/eltiro/runs.py", "src/datumoj/eltiro/rematch.py", "src/datumoj/eltiro/alerts.py", "src/datumoj/eltiro/gaps.py", "src/datumoj/eltiro/package.py", "src/datumoj/eltiro/license_gate.py", "src/datumoj/eltiro/model_match.py", "src/datumoj/eltiro/concept_notes.py"]
protected: ["tests/fixtures/**", "src/datumoj/eltiro/testing/**", "docs/plans/0.4-wisteria-samples/**"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_wisteria_810.py
    - cmd: make check T=tests/eltiro
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-WISTERIA-810/summary.md 完成说明（含页面解析规则、更新怎样判定、草稿里哪些字段由规则填、哪些由模型建议、哪些留给人，V07 和 #002 两个案例的草稿）"]
  review: {by: human, checklist: ["页面内容是否只当数据、不执行其中的指令", "草稿是否从不自动登记", "模型建议是否标注模型生成、调用是否经过 806 的路由和许可", "来源对应是否只按注册表、对不上的明说未接入", "规则填不了的字段是否留空并列出，没有臆造国家代码、表名和数值", "更新判定是否只按案例内容哈希", "草稿是否只写在 Eltiro 数据目录", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
把需求页面（https://plan.datumoj.com/verify/ 的 30 个验证案例、https://plan.datumoj.com/cases/ 的 100 个融合案例）里的案例转成需求定义草稿。页面会持续更新：新增或改动的案例要能找出来，重新出草稿。草稿只是起点，人改好后用现有的 `eltiro submit` 提交，本任务不登记任何需求。

# 背景
开发线：Eltiro。设计见 `docs/plans/0.4-wisteria.md` 第 3.1 节（需求从哪里来）、第 6.1 节（需求定义）、第 6.7 节（模型路由）。
- 页面的源文件在 `main` 分支的 `plan/verify/index.html`、`plan/cases/index.html`。每个案例是一个 `<article class="case">`，两个页面的字段**不一样**：
  - verify 页面：编号（`.no`，如 `V07`）、标题（`h3`，"国家 · 指标"）、假设（`.hyp`）、来源链接（`.srcs a.src`）、方法（`.method`）、判定信号（`.signal`）；
  - cases 页面：编号（`.no`，如 `#002`）、分类（`.stag`）、标题（`h3`）、问题（`.q`）、来源链接（`.srcs a.src`）、融合方法（`.fuse`）、Aha（`.aha`）。
- 第 1 次尝试按两个页面同一结构实现，真实的 cases 页面 100 个案例全部解析失败（verify 页面 30 个都成功）。这次从 `feature/0.4` 重做，按下面"重做要求"改正。
- 已完成的实现：001 的 `DemandSpec`；801 的校验和 `eltiro submit`；806 的路由、缓存和提供方，路由配置可以加任意步骤名（本任务用 `demand_draft`，配置里没有就当 `none`）。
- 需求样例在 `docs/plans/0.4-wisteria-samples/`（V07 和 #002），可作为草稿的对照。

本版本在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- 本任务新建 `page_cases.py`（页面解析和更新记录）和 `drafts.py`（草稿生成和存储）。
- `cli.py` 本轮不归本任务，**不加命令行**，只提供 Python 接口；命令行由 811 接上。同时在跑的 805、807 用各自的文件，都不要碰。
- 806 的文件只调用、不改；Eltiro 的模型、契约和测试工具只读；Rikolto 的代码一律不改。需要改契约或模型时，写进 `summary.md` 并停下来问。
- 本任务不联网：页面内容由调用方以字节传入（宿主读本地文件或自己下载）。
- HTML 解析用标准库 `html.parser`，不加依赖。
- 回归测试写在 `tests/regressions/test_audit_wisteria_810.py`；调度器不接受 `skip` / `xfail`。
- 测试全部离线，用合成的小页面；不调用真实模型，用记录调用的假提供方。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、账号 ID、真实主机名或内网地址、本机绝对路径。

# 要求
1. **页面解析**（`page_cases.py`）：
   - 从页面字节解析出案例列表，按页面种类取各自的字段（见"背景"）：verify 取编号、标题、假设、来源链接（文字和 URL）、方法、判定信号；cases 取编号、分类、标题、问题、来源链接、融合方法、Aha。
   - 页面内容是不可信数据：限制大小和案例数，去掉脚本和样式，只取上面这些字段的纯文本；文字里的任何指令都不执行。
   - 解析不出编号或字段的案例记为解析失败并列出，不猜。
   - **重做要求**：真实的两个页面都要能解析：verify 30 个、cases 100 个全部成功（以合并时 `main` 上的页面为准，写进 `summary.md`）。
2. **更新记录**：
   - 每个案例按规范化后的内容算哈希，存进 `<data>/eltiro/page_cases.sqlite`（编号、种类、哈希、首次和最近看到的时间由调用方传入）。
   - 再次读入同一页面时报告：新增、改动、消失、未变。只按内容哈希判定。
   - **重做要求**：部分案例解析失败时，仍记录解析成功的案例，失败的单独列出；失败的案例保持上次的记录，不算"消失"。只有整页无法解析（编码、大小、结构上限）时才整页拒绝。
3. **草稿生成**（`drafts.py`）：
   - 由规则填：`id`（由编号生成）、`consumer`（调用方指定）、`version`、用途说明（verify 取假设和方法的原文，cases 取问题和融合方法的原文）、偏好来源（来源链接按注册表里来源的官方域名对应；对不上的列为"未接入来源"，不编来源 id）。
   - 可选模型建议（步骤 `demand_draft`）：实体、指标说明、时间范围和频率、运算。调用一律经过 806 的 `plan` / `invoke`；页面内容的许可由宿主配置传入，缺省按最严处理（不进云端）。模型建议的字段逐个标注模型生成，并记下提供方和模型版本。
   - 规则和模型都填不了的字段留空，在草稿里列成"待人补"清单。不臆造国家代码、表名和数值。
   - 草稿写成 YAML，存到 `<data>/eltiro/drafts/<编号>-<内容哈希前缀>.yaml`，原子写入；同一内容重复生成得到相同文件。
   - 草稿文件明确标为草稿（例如顶层 `draft: true`），按 801 校验时会被拒绝，必须由人删掉草稿标记、补全后再用 `eltiro submit` 提交。
4. **Python 接口**：`parse_cases(page_bytes, kind)`、`record_cases(...)`（返回新增 / 改动 / 消失 / 未变）、`draft_for_case(case, *, consumer, registry, router=None, ...)`、`list_drafts(...)`。
5. **回归测试** `test_audit_wisteria_810.py` 覆盖：
   - 按页面结构解析出全部字段；缺字段的案例记为失败、不猜；
   - **重做要求**：测试夹具用两个真实页面的结构，各截取几个案例原样放进测试（至少含 V07 和 #002，页面是本项目自己的内容，可以放）；verify 和 cases 各自的字段都断言到；
   - 部分案例失败时成功的照常记录、失败的不算消失；
   - 页面里夹带的"请登记""忽略规则"之类文字不产生任何动作，只作为文本留在用途说明里；
   - 超大页面和超多案例被拒绝；
   - 新增、改动、消失、未变判定正确，只按内容哈希；
   - 来源链接只对应到注册表里的来源，对不上的列为未接入；
   - 不配模型时草稿只有规则填的字段和"待人补"清单；配假模型时建议字段标注模型生成；
   - 页面许可缺省时内容不进云端提供方；
   - 草稿按 801 校验会被拒绝，从不写进需求注册表；
   - 草稿只写在 Eltiro 数据目录，重复生成文件不变；
   - 用 V07 和 #002 的合成页面生成草稿，规则字段和样例需求一致。
6. `make check T=tests` 全部通过。

# 不做
- 不联网下载页面，不加命令行（811），不做管理页面（601）。
- 不自动登记需求，不改 801 的校验规则。
- 不改 806 的路由、缓存、提供方和提示词。
- 不改 Rikolto 的代码，不改 Eltiro 的模型和契约。
