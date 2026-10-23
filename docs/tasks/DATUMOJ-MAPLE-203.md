---
id: DATUMOJ-MAPLE-203
title: 解析器修正：PDF 日期传到每个 Chunk（D-06）；HTML 去框架改用 drop_tree() 保留尾随正文
project: DATUMOJ
status: todo
priority: 1
depends_on: ["DATUMOJ-MAPLE-002"]
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/parsers/pdf.py", "src/datumoj/parsers/html_page.py", "tests/parsers/test_pdf_dates.py", "tests/parsers/test_html_page_tail.py", "tests/test_evidence_regressions.py", "results/DATUMOJ-MAPLE-203/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/parsers/test_pdf_dates.py
    - cmd: make check T=tests/parsers/test_html_page_tail.py
    - cmd: make check T=tests/parsers
    - cmd: make check T=tests/test_evidence_regressions.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-203/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
PDF 的发布日期出现在它的每个检索块上，按日期检索不再漏掉 PDF；HTML 页面去掉页眉页脚等框架时，不会把紧跟在框架元素后面的正文一起丢掉。

# 背景
开发线：B 文档线。审核 D-06：`parsers/pdf.py` 在读取 `meta.published_at` 之前就建了 Chunk，日期只写进 Document；检索的日期过滤和引用读的是 Chunk 的日期。CodeRabbit 在 #15 指出：`parsers/html_page.py` 用 lxml 的 `remove()` 去掉框架元素，会把该元素的 `tail`（紧随其后的文本）一起删掉，应改用 `drop_tree()`。

计划见 `docs/plans/0.3-maple.md`（第 6.0 节是本检查点 m0 的问题表，总计划 `docs/PLAN.md`）。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。身份与状态的约定见 `docs/adr/0004-evidence-identity.md`（由 MAPLE-002 写出）。除契约外，只能 import 已有模块和本任务 `depends_on` 里已合并任务的模块。
- 审核问题的回归用例在 `tests/test_evidence_regressions.py`（MAPLE-002 写出，修好之前标 `xfail(strict=True)`，原因里写着负责的任务号）。修好本任务负责的用例后**删掉它们的 xfail 标记**，不改断言；其他任务的用例不动。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-203/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. PDF：Document 的 `published_at` / `effective_at` 同样写到每个 Chunk；解析器版本号加一（使已入库的 PDF 在下次解析时重做）。
2. HTML：所有删除框架元素的地方改用 `drop_tree()`（或等价地保留 tail）；解析器版本号加一。
3. 新测试：PDF 有 meta 日期时每个块都有日期、没有时都为空；HTML 样本里框架元素后紧跟正文文本时，正文保留在结果里。现有解析器测试必须仍然通过（版本号断言随之更新）。删掉 `tests/test_evidence_regressions.py` 里本任务负责的用例（xfail 原因里写着本任务号）的 xfail 标记，它们必须通过。全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不改其他解析器。
- 不做 OCR。
