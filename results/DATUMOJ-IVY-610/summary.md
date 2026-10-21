# DATUMOJ-IVY-610 完成说明

已新增 `datumoj admin [--port 8765] [--data-dir …]` 本机管理页面，服务固定监听 `127.0.0.1` 并打印访问网址。页面显示总开关、来源状态、调度时间、最近运行结果、快照数和 raw 字节估算；表单只用带随机 CSRF 令牌的 POST。控制及执行委托给 `core.schedule`，立即运行在后台线程触发，并由 107 的开关和来源锁再次检查。运行线程内重新组装抓取依赖，避免跨线程复用存储连接。

所有来源名称、ID、调度值和运行记录文本均经过 HTML 转义。页面快照见 [admin.html](admin.html)。

## 变更文件

- `src/datumoj/admin/__init__.py`
- `src/datumoj/admin/web.py`
- `src/datumoj/cli.py`
- `tests/test_admin.py`
- `results/DATUMOJ-IVY-610/admin.html`
- `results/DATUMOJ-IVY-610/tests.log`
- `results/DATUMOJ-IVY-610/summary.md`

## 验收

- `make check T=tests/test_admin.py`：8 passed
- `make check T=tests/test_cli_schedule.py`：14 passed
- 保护测试 `make check T=tests/test_cli.py`：4 passed
- 保护测试 `make check T=tests/core/test_schedule.py`：24 passed

环境中 `uv` 不可用，`make check` 按仓库配置回退到只读主仓库 `.venv`，所有命令均通过。自动验收与保护测试的输出记录在 [tests.log](tests.log)。人工评审 checklist 尚待审核。

未做：登录、多用户与外网监听（按任务要求排除）。未核对：人工评审 checklist；已知风险：暂无。
