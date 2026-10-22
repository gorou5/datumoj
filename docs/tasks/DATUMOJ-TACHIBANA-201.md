---
id: DATUMOJ-TACHIBANA-201
title: PDF 解析器：按页切块、needs_ocr、加密和损坏处理
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-TACHIBANA-001"]
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/parsers/pdf.py", "tests/parsers/test_pdf.py", "results/DATUMOJ-TACHIBANA-201/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/parsers/test_pdf.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-TACHIBANA-201/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
政策网页链接的 PDF 能解析成按页的文本块，引用能定位到第几页；扫描件标记为需要 OCR，加密和损坏的 PDF 记为解析失败。

# 背景
开发线：B 文档线。计划 6.2 节。样本在 `tests/fixtures/gov_web/pdf/`（H1 录制，说明见该目录 README：文本型、扫描件、加密件各至少一份，另有一份截断的损坏文件）。用 pypdfium2 抽文本（已锁定）。解析器按插件约定放进 `datumoj.parsers`，按 `(source_kind, content_type)` 被选中。

计划见 `docs/plans/0.2-tachibana.md`（总计划 `docs/PLAN.md`；与团队规范 `AGENTS.md`、`docs/interoperability.md` 的取舍见 PLAN 第 1.1 节）。本版本在 `feature/0.2` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块，以及 IVY 已有的模块；其他 TACHIBANA 任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-TACHIBANA-201/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。

# 要求
1. 解析器声明 `source_kind = "gov_web"`、`content_type = "application/pdf"`，有 `name` 和 `version`。
2. 每页一个 `Chunk`，`locator` 为 `page=<从 1 开始的页码>`，正文经 `datumoj.text` 的 `normalize`；`Document.kind = pdf`，`doc_key` 为 `<source_id>:pdf:<规范化 URL 的 sha1 前 16 位>`（同一 URL 两次抓取得到同一 doc_key）。
3. 标题：PDF 元数据的 Title（非空且不是乱码时），否则第一页第一行有意义的文字，再否则 URL 的文件名。
4. 文字少于 50 个字符（去空白后）的页标记为需要 OCR：不产出 Chunk，加 warning `needs_ocr page=N`；`Document.metadata` 记 `needs_ocr_pages`（页数）和 `pages`（总页数）。全部页都需要 OCR 时仍产出 Document（没有 Chunk），并加 warning `needs_ocr all`。
5. 加密的 PDF 抛 `ParseError`，信息含“encrypted”；损坏或截断的 PDF 抛 `ParseError`，不能让底层异常直接冒出去。
6. 快照 meta 里的父子关系（契约 `parent_ref`）和 `published_at`（如有，`YYYY-MM-DD`）原样写进 `Document.metadata` / `Document.published_at`；没有日期就留空（回答时按“无数据日期”处理），不能拿抓取日顶替。
7. 测试覆盖每条，用 fixture 里的真实样本；通过契约里 Parser 的合格测试（如有）。

# 不做
- 不做 OCR，不抽表格（pdfplumber 表格抽取留到以后）。
- 不改采集器和接线。
