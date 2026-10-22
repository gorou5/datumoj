# DATUMOJ-TACHIBANA-102 完成说明

## 实现

- `src/datumoj/core/monitor.py`：新增纯提醒计算函数与 `Alert` 模型；实现连续失败、解析失败率上升、超期未成功、短保留来源断档四类规则。失败快照经 `classify_failure` 汇总到每条提醒的 `details`，并记录可重试与不可重试计数。时间与规则阈值可注入或覆盖。
- `src/datumoj/core/schedule.py`：来源状态增加 JSON 可序列化的 `alerts`，顶层增加 `alert_count`；`Scheduler` 接收可选的 `Manifest` 契约实例。
- `src/datumoj/admin/web.py`：来源表格上方增加按严重程度排序的提醒区；动态显示文本继续通过 `_e` 转义。管理应用将现有 `deps.manifest` 绑定到尚未设置 manifest 的调度器。
- `tests/core/test_monitor.py`、`tests/test_admin_alerts.py`：覆盖四类提醒的触发/不触发、次数与时间边界、短保留判定、状态 JSON 和管理页面顺序及转义。
- `results/DATUMOJ-TACHIBANA-102/tests.log`：保存任务验收命令输出。

## 验收

任务列出的五条 `make check` 命令全部通过，输出见 `tests.log`。测试使用项目内假 Manifest 和注入时钟，没有联网。`uv` 在沙箱中不可用，Makefile 按项目约定改用 `/Users/aaron/workspace/datumoj/.venv/bin/python`。

本任务未实现 Manifest 协议，因此没有单独的协议合格测试套件需要继承；提醒逻辑通过项目 `Manifest` 契约和 `datumoj.testing.fakes.FakeManifest` 使用解析统计与失败快照。

## 范围与限制

只修改任务 scope 中的源文件、测试和结果文件，没有修改 CLI、契约、运行记录格式或开关/锁行为。现有 CLI 会把 `Scheduler.status()` 返回的提醒字段序列化到 JSON；但 `src/datumoj/cli.py` 在 scope 外，现有 CLI 构造调度器时没有传入 `Manifest`。因此 CLI 的 `schedule status` 可显示基于运行记录的提醒，解析失败率提醒及按 `FailureKind` 分类的详情需要 CLI 后续在构造 `Scheduler` 时传入已有 `deps.manifest`。管理页面已绑定该 Manifest，可完整计算这些提醒。

没有核对未列入验收的全仓库测试或人工抽样页面。当前主要风险是上述 CLI Manifest 注入缺口；要让 CLI 也计算解析失败率与失败类别，需在允许范围后修改 CLI 构造点。
