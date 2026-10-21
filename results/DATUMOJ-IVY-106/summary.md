# DATUMOJ-IVY-106 完成说明

执行器现在在调用 `classify` 前识别抓取器返回的 `too_large`，记录失败并在完成 `after_fetch` 后停止。采集器将某个响应分类为 `too_large` 时，该正文不计入来源累计容量；执行器记录失败快照、调用回调并继续后续任务。`max_files` 处理保持不变。

新增离线回归测试覆盖：单文件超限后继续抓取、累计容量超限后停止、抓取器层超限在分类前停止，以及这些结果的 `after_fetch` 调用。原有 runner 测试也已通过。

## 验收

- `make check T=tests/core/test_runner_file_cap.py`：3 passed。
- `make check T=tests/core/test_runner.py`：46 passed。
- 完整输出见 [acceptance.log](acceptance.log)。沙箱中 `uv` 不可用，`make check` 按项目约定改用主仓库只读 `.venv` 执行。

## 变更文件

- `src/datumoj/core/runner.py`
- `tests/core/test_runner_file_cap.py`
- `results/DATUMOJ-IVY-106/summary.md`
- `results/DATUMOJ-IVY-106/acceptance.log`

跳过：未运行全量测试套件。未核对：scope 外的文档与配置。风险/兼容点：为保持受保护的 105 测试通过，累计字节恰好达到上限时仍沿用原规则停止；只有严格超过时才将当前结果改记为累计容量导致的 `too_large`。
