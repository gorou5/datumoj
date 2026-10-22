---
id: DATUMOJ-TACHIBANA-701
title: 评测题：政策网页 12、注入 3、IVY 遗留缺口 5
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-TACHIBANA-201", "DATUMOJ-TACHIBANA-202", "DATUMOJ-TACHIBANA-205"]
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["eval/questions/gov_web.yaml", "eval/questions/tachibana_gaps.yaml", "tests/eval/test_questions_tachibana.py", "results/DATUMOJ-TACHIBANA-701/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "eval/questions/law.yaml", "eval/questions/catalog.yaml", "eval/questions/stats.yaml", "eval/questions/behavior.yaml"]
acceptance:
  automated:
    - cmd: make check T=tests/eval/test_questions_tachibana.py
    - cmd: make check T=tests/eval
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-TACHIBANA-701/summary.md 完成说明"]
  review: {by: human, checklist: ["题目是否都能在 fixture 里找到依据", "是否覆盖政策网页、PDF、时间语义、拒答、注入、遗留缺口", "期望是否写得可机器判断", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
TACHIBANA 的新能力有对应的离线评测题，门槛测试（903）能用它们判断达标。

# 背景
开发线：G 评测线。计划第 3 节第 8 条。题目格式见 `eval/questions/*.yaml` 和 `src/datumoj/eval/framework.py`（严格模型，未知字段会报错）。政策网页题基于 `tests/fixtures/gov_web/` 的样本（README 里有每个页面的 URL 和用途）：真实页面只有内阁府、总务省、大阪市三个站点和内阁府的 `text.pdf`（东京都、大阪府、经产省没有样本，不能出题）；注入题用合成页 `html/synthetic/injection.html`，日期题可用 `html/synthetic/dated-wareki.html`；变化题（5 题）需要框架支持 `list_changes`，放在 903 一起做。

计划见 `docs/plans/0.2-tachibana.md`（总计划 `docs/PLAN.md`；与团队规范 `AGENTS.md`、`docs/interoperability.md` 的取舍见 PLAN 第 1.1 节）。本版本在 `feature/0.2` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块，以及 IVY 已有的模块；其他 TACHIBANA 任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-TACHIBANA-701/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。

# 要求
1. `gov_web.yaml` 12 题：日中英三种语言都有；覆盖检索到正确的节（`section=`）和 PDF 页（`page=`）、更新日的时间语义（问“现在”时要求出处带日期，抽不到日期的页要说明无数据日期）、跨站点比较（例如内阁府和大阪市的创业支援），以及 1 题“补助是否适用于我”的拒答（不判断个案）。期望命中用 `doc_key` 前缀或 URL，必须在 fixture 里真实存在。
2. `tachibana_gaps.yaml` 8 题：注入 3 题（基于 `injection.html` 里的“指令样式”文本：回答不执行、出处标为来源正文）；IVY 遗留缺口 5 题：关键词常见词误命中应判 `zero_hits` 2 题、e-Stat 表能被 `search_catalog` 找到 2 题、目录结果的出处能用 `cite` 还原 1 题。
3. 期望命中必须和已合并的解析器的真实产出一致（第 1 次尝试按猜的格式写，被打回）：政策网页和 PDF 的来源 ID 都是 `jp-gov-web`（205 的 `sources/jp-gov-web.yaml`，不按站点分来源）；网页的 `doc_key` 是 `jp-gov-web:page:<规范化 URL 的 sha1 前 16 位>`（202，`parsers/html_page.py`），PDF 是 `jp-gov-web:pdf:<同样规则的哈希>`（201，`parsers/pdf.py`）；网页的 `locator_prefix` 用 202 实际产出的 `section=<标题路径>`（不是序号），PDF 用 `page=<页码>`。真实页面的 URL 取 `tests/fixtures/gov_web/README.md` 文件清单里的原始 URL；合成页面没有真实 URL，统一用 `https://example.invalid/gov-web/synthetic/<相对 html/synthetic/ 的路径>`，并在题目 notes 里写明（903 回放时用同样的 URL）。e-Stat 的题保持现状（`jp-estat-files:<statInfId>`）。
4. 新测试：两个文件都能被 `load_questions` 加载，题目 ID 唯一；对每道政策网页、PDF、注入题，用上面的 URL 构造快照，调用已合并的 202 / 201 解析器解析对应 fixture，断言期望的 `doc_key` 存在，且有 chunk 的 locator 以期望的 `locator_prefix` 开头（不能手抄哈希而不验证）；`tests/eval` 必须仍然通过。

# 不做
- 不改评测框架和已有题目。
- 不在本任务里跑门槛（903 做）。
