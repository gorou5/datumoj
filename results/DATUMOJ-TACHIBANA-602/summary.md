# DATUMOJ-TACHIBANA-602 完成说明

## 修改文件

- `src/datumoj/analysis/reports.py`：按来源和 `ChangeKind` 生成总览及逐事件 Markdown；用 `Citation.render()` 输出出处；对来源文本转义 Markdown；统计本周变化 PDF 的 OCR 页数；列出提醒和 `retention_gap` 来源。
- `src/datumoj/cli.py`：新增 `datumoj report weekly`，支持日期和输出路径选项；默认写入 data dir 下按 ISO 周命名的文件；从 `Scheduler.status()` 汇总提醒。将 manifest 注入调度器以提供完整提醒数据。
- `tests/analysis/test_weekly_report.py`：覆盖五类事件、引用/失败回退、来源分组、Markdown 转义、OCR 范围、空周、CLI 路径与 Scheduler 提醒。
- `results/DATUMOJ-TACHIBANA-602/acceptance.log`：验收命令及输出。

## 实现与核对

- 报告只通过 `ChangeLog`、`Manifest`、`ChunkStore`、`Registry` 契约读取数据；不读取调度记录或开关。CLI 通过 `Scheduler.status()` 传入提醒。未调用 LLM。
- 无事件 locator 时，如果存有文档块，使用该快照第一个真实块的 locator 作为可还原的文档快照引用位置，并在报告中说明；没有事件快照或 manifest 无对应成功快照时，只输出资源标识、检测时间和事件/失败类型，不拼造出处。
- OCR 页数按变化事件关联的 PDF `doc_key` 去重；metadata 缺字段或非数字时不纳入分子/分母。
- 只改任务 scope 内文件。没有实现契约协议，因此没有对应的协议实现合格测试套件可跑；测试使用 `datumoj.testing` 假实现。测试均离线。

## 验收

- `make check T=tests/analysis/test_weekly_report.py`：通过，4 passed。
- `make check T=tests/test_cli.py`：通过，4 passed。
- 两条命令的 `make check` 提示 uv 不可用并切换到主仓库 `.venv`；测试结果通过。
- `git diff --check`：通过。

## 待确认与风险

- `Citation.locator` 是必填字段，契约和 `cite` 没有通用的整份文档 locator。如果事件 locator 为空且 manifest 能找到成功快照、但 `ChunkStore` 也没有该文档的任何块，就无法生成合法 Citation 而不伪造 locator。当前报告会明确写“快照已找到，但文档没有可还原的 locator”。请确认这个边界处理是否接受；若要求仍必须生成 Citation，需要先由契约约定可被 `cite` 还原的整份文档 locator。
- 未核对完整仓库测试套件；只运行任务列出的两条验收命令。
- 跳过：主动推送、LLM 摘要和任何 scope 外修改。未发现联网测试；其他风险为上述 locator 契约缺口。
