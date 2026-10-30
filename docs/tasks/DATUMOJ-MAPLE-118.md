---
id: DATUMOJ-MAPLE-118
title: 调度频率增加 weekly：到期判断、监控提醒和管理页一致
project: DATUMOJ
status: done
priority: 2
depends_on: ["DATUMOJ-MAPLE-117"]
runner: claude
model: null
tier: low
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/core/schedule.py", "src/datumoj/core/monitor.py", "src/datumoj/admin/web.py", "sources/README.md", "tests/core/test_schedule.py", "tests/core/test_schedule_due.py", "tests/core/test_monitor_cloud.py", "tests/test_admin.py", "tests/regressions/test_audit_118.py", "results/DATUMOJ-MAPLE-118/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example", "sources/*.yaml"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_118.py
    - cmd: make check T=tests/core/test_schedule_due.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-118/summary.md 完成说明"]
  review: {by: other_family, checklist: ["weekly 在到期判断、云端缺口提醒、管理页三处是否一致", "频率表是否只定义一处", "未知频率是否仍然报错", "已有频率的行为是否不变", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
来源配置里可以写 `schedule: weekly`：每 7 天到期一次，监控按 7 天判断“超过一个周期没有成功运行”，管理页显示“每周”。

# 背景
开发线：A 云端运行与运行基础。0.3 新来源（e-Stat API、Eurostat、日本四个文件来源）计划按周更新，但现在只支持 `hourly`、`daily`、`monthly`，频率表在三处各写一份：
- `core/schedule.py` 的 `next_due`：不认识的频率直接 `ValueError("不支持的调度")`，写 `weekly` 会让调度报错；
- `core/monitor.py` 的 `_SCHEDULE_INTERVALS`：不在表里的来源不参与 `cloud_run_missing` 判断；
- `admin/web.py` 的显示标签。

计划见 `docs/plans/0.3-maple.md`，总计划 `docs/PLAN.md`。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。本任务不改契约；确实需要时写进 `results/DATUMOJ-MAPLE-118/summary.md` 并停下来问。
- 回归测试写在 `tests/regressions/test_audit_118.py`，断言修好以后的正确行为；调度器不接受 `skip` / `xfail`。
- 测试全部离线，临时文件一律放 `tmp_path`，不写仓库里受 git 跟踪的路径。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。
- `tests/fixtures/` 下已有的样本只读。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、账号 ID、真实主机名或内网地址、本机绝对路径。

# 要求
1. 频率表只定义一处（放在 `core/schedule.py`），`monitor.py` 和 `admin/web.py` 从这里取；增加 `weekly = 7 天`。
2. `next_due`：`weekly` 首轮立即到期，之后上次开始时间 + 7 天；未知频率仍然报错；`manual` 和空仍然永不到期。
3. 监控：`weekly` 来源超过 7 天没有成功运行时报 `cloud_run_missing`，未超过时不报；其他频率的判断不变。
4. 管理页显示“每周”。`sources/README.md` 的 `schedule` 字段说明列出可用值。
5. 回归测试覆盖要求 2、3 和管理页标签；已有频率的到期和提醒行为不变。
6. `make check T=tests` 全部通过。

# 不做
- 不改任何 `sources/*.yaml` 的频率（新来源的 yaml 在各自任务里写）。
- 不做按星期几、按时刻或按时区的调度。
- 不改 `cloud_stalled` 的 2 小时阈值。
