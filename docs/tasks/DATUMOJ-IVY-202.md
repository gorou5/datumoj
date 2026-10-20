---
id: DATUMOJ-IVY-202
title: 法令 XML 解析器：按条切分，带层级和 locator
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-IVY-002", "DATUMOJ-IVY-003"]
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/parsers/xml_law.py", "tests/parsers/test_xml_law.py", "results/DATUMOJ-IVY-202/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/parsers/test_xml_law.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-202/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
实现法令的 `Parser` 插件：一部法令 XML 变成一个 Document 和按条切分的 Chunk。

# 背景
开发线：B 文档线。元素：`Law`、`LawNum`、`LawTitle`、`Part`、`Chapter`、`Section`、`Article`、`ArticleCaption`、`ArticleTitle`、`Paragraph`、`Item`、`SupplProvision`、`AppdxTable` 等。样本在 `tests/fixtures/egov_law/`。

计划见 `docs/PLAN.md`，任务总表、开发线和跨任务约定见 `docs/TASKS.md`。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块（例如 304 用 302 的地区码表）；其他任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-IVY-202/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。

# 要求
1. 安全解析：`resolve_entities=False`、`no_network=True`、禁止加载 DTD、限制输入大小。
2. 每条一个 Chunk：`title_path` 含法令名和编章节条标题；locator `art=27`；条文超过 2000 字时按项再切（`art=27;para=2`）。
3. 附则：locator 前加 `suppl=<修正法令番号或 0>`；别表：`table=<序号>`。条号用 `article_number`（`第二十七条の二` → `art=27_2`）。
4. `doc_key` = `jp-egov-law:<法令ID>`；Document 记录法令番号、法令名、施行基准日（同时作为 Document 和每个 Chunk 的 `effective_at`），以及采集器给出的未施行修改列表（格式见下面的说明）。修订历史不说明影响哪几条，所以**不做逐条标记**，只在文档级记录。
5. 测试：带章节的法令、条の二、附则、别表、含实体声明的恶意 XML 被拒。
6. （契约二对齐）实现 `Parser` 协议：`parse(snapshot, data, meta=None)`，从 `meta` 读施行基准日和未施行修改；`chunk_id` 用 `<doc_key>#<locator>`，同一法令跨快照保持稳定；坏输入抛 `ParseError`。以 `ParserSuite` 为准。
7. （法令 meta 格式，201 与 202 共用）`meta` 为 `{"role": "law_data", "law_id": "<法令ID>", "asof": "YYYY-MM-DD", "pending_amendments": [{"amendment_law_num": str|null, "promulgate_date": "YYYY-MM-DD"|null, "enforcement_date": "YYYY-MM-DD"|null, "enforcement_comment": str|null}]}`（以上是必有的键，可以有其他键，例如 `law_revision_id`、`amendment_law_title`；解析器只依赖必有的键）。解析器写入 `Document.metadata["asof"]`（日期字符串）和 `Document.metadata["pending_amendments"]`（上面列表的 JSON 字符串，`ensure_ascii=False`，没有时为 `"[]"`）；`meta` 缺失或没有 asof 时 `effective_at` 为空，不要猜。测试里自己构造 meta，不必等 201。

# 不做
- 不改 scope 以外的文件。
