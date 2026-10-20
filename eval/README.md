# 离线评测题库

题库由 UTF-8 YAML（`.yaml` / `.yml`）或 JSON（`.json`）文件组成。`load_questions(dir)` 递归读取目录；每个文件可包含单道题、题目列表，或 `{questions: [...]}`。题目 ID 在整个目录内必须唯一。校验失败会指出文件和题号。

```yaml
questions:
  - id: population-tokyo-2020
    question: 2020 年東京都の人口はいくらですか？
    lang: ja
    kind: numeric
    queries:
      - 東京都 2020年 人口
      - 東京の人口 2020
    source_ids: [jp-stat] # 可选；指定 Retriever 的来源范围，缺口题尤其有用
    expect:
      hits:
        - doc_key: jp-stat:population
          locator_prefix: table=population;row=tokyo
      value:
        sql: "SELECT value FROM observations WHERE obs_id = 'tokyo-population-2020'"
        equals: 14047594
        tolerance: 0
    must_not: []
    notes: 来源中的数值应作为原始数据证据出现。
```

## 字段

- `id`：稳定且唯一的题目 ID；`question`：供阅读的自然语言问题；`lang`：语言标签，例如 `ja`、`en`。
- `kind`：`retrieval`、`numeric`、`gap`、`time`、`injection` 或 `cross_source`。
- `queries`：至少一个改写后的日文检索查询，会作为一组传给注入的 `Retriever`。其他语言的题目也可以提供日文查询。
- `source_ids`：可选来源 ID 列表，作为 `Retriever.search` 的过滤条件；设置后可评测 `not_ingested`、`fetch_failed`、`out_of_scope` 等来源相关缺口。
- `expect.hits`：预期命中列表；每项含 `doc_key`，可选 `locator_prefix`。前缀按 locator 段匹配，例如 `table=population` 匹配 `table=population;row=1`，不会匹配 `table=population-old`。
- `expect.value`：数值题的只读 SQL、期望标量 `equals` 和非负绝对容差 `tolerance`。SQL 必须由契约中的 `ObservationStore.query_readonly` 执行；仅接受一个结果行的第一列作为标量。
- `expect.gap_state`：期望的契约缺口状态（`ok`、`zero_hits`、`not_ingested`、`fetch_failed`、`out_of_scope`）。
- `must_not`：注入题中用于跟踪的不可信注入文本。评测要求每条文本至少出现在一个命中 `SourceText.content` 中，且不出现在结果的其他字段。
- `notes`：供评测维护者阅读的备注，不参与计算。

`expect` 可按题型只提供适用的子项；检索、数值、时间、注入和跨来源题必须有非空 `expect.hits`，数值题还必须有 `expect.value`，缺口题必须有 `expect.gap_state`，注入题必须有非空 `must_not`。跨来源题的预期命中至少覆盖两个来源。

## 执行、指标和报告

`evaluate(questions, retriever=..., observation_store=..., cite=...)` 只依赖 `Retriever`、`ObservationStore` 契约和注入的 `cite(Citation)` 函数，不创建网络客户端。`cite` 应返回还原出的原文片段或数据行；对每题结果的前 3 条命中逐条调用。SQL 数值精确率比较查询标量和 `equals`（应用绝对容差）；数值出现率要求期望数值也能在已还原的引用原文或与该引用来源、快照、locator 对应的 SQL provenance 数据行中找到。数字先做 NFKC 规范化、去千位分隔符，再按数字边界比较。

报告包括总体、按来源、按语言指标、逐题指标和失败题列表。使用 `write_reports(report, dir)` 写出 `evaluation.md` 与 `evaluation.json`。每个分数在 0 到 1 之间；没有适用题目的指标显示 `N/A`。

```python
from datumoj.eval import check_gate, evaluate, load_questions, write_reports

questions = load_questions("eval/questions")
report = evaluate(questions, retriever=retriever, observation_store=observations, cite=cite)
write_reports(report, "results/eval")
gate = check_gate(report)
if not gate.passed:
    print("\n".join(gate.failures))
```

默认门槛为 Recall@10 ≥ 0.85、数值精确率 = 1.0、数值出现率 = 1.0、缺口状态正确率 = 1.0、引用可还原率 ≥ 0.95。可通过 `check_gate(report, thresholds={...})` 覆盖部分门槛；未评测的默认门槛指标会使门槛失败，避免题库缺类时误报通过。

时间语义指标检查相关命中的 `Citation` 是否带统计期间或发布日。引用可还原率检查 `cite` 是否返回非空证据。机器无法可靠判断引用是否真正支持结论；该项留给 H6 人工抽查，不计入自动门槛。
