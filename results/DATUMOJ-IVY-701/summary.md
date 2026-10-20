# DATUMOJ-IVY-701 完成说明

## 实现

- 在 `eval/README.md` 记录 YAML/JSON 题库格式、字段含义、来源过滤、题型校验、指标口径、报告和默认门槛。
- `load_questions(dir)` 递归加载题库，校验字段、题型、locator、重复 ID；错误包含文件路径和题号/ID。
- `evaluate(...)` 通过 `Retriever`、`ObservationStore` 契约及注入的 `cite(Citation)` 函数运行：Recall@10、MRR、只读 SQL 数值精确率、引用数值出现率、缺口状态、时间语义、注入文本隔离和引用可还原率。
- `check_gate(report, thresholds)` 实施默认门槛，并把没有评测题目的必需指标判为未通过。
- `write_reports(report, dir)` 生成 Markdown 与 JSON 报告，包含总体、按来源、按语言和失败题列表。
- 测试使用 `datumoj.testing` 假检索器、假观测库及 15 道假题覆盖全部指标、四类缺口、报告和门槛判断。

## 要求与复核

- 只改任务 scope 内文件；未改契约、依赖、夹具或任务外文件。
- 运行时只依赖 core 契约和模型；测试使用 `datumoj.testing`。没有实现新协议，因此没有对应的新合格测试套件需要继承；本次指定测试未访问网络。
- 默认门槛为 Recall@10 0.85、数值精确率 1.0、数值出现率 1.0、缺口状态正确率 1.0、引用可还原率 0.95。自动评测不判断引用是否真正支持结论，按任务要求留给 H6 人工抽查。
- 任务材料没有“评审中的高置信度质疑”一节，此项不适用。

## 验收

执行 `make check T=tests/eval/test_framework.py`：6 passed。沙箱中 `uv` 不可用，Makefile 按既有回退逻辑使用主仓库 `.venv`；最终输出记录在 `acceptance.log`。

跳过：没有生产题库或实际检索器可用于跑真实数据报告。未核对：H6 人工抽查尚未执行。风险：自动数字出现率按规范化后的数字 token 比较，不代替人工判断数值与结论的语义对应关系。
