# DATUMOJ-TACHIBANA-208 完成说明

## 修改内容

- `src/datumoj/crawlers/gov_web.py`：新增可选的 `scope.pdf_recheck_days` 校验；按注入时钟 `ctx.now` 对比 PDF 最近一次成功快照。周期内的 PDF 加入本次去重集合但不生成任务、不消耗种子上限，并记录 `gov_web pdf_recheck skipped=N`。
- `sources/jp-gov-web.yaml`：设置 PDF 复查周期为 7 天，并启用 `head_probe`。
- `tests/crawlers/test_gov_web.py`：更新来源配置断言。
- `tests/crawlers/test_gov_web_recheck.py`：覆盖 6/8 天、无成功快照、未配置周期、非法值、网页任务、去重、预算和日志。
- `tests/integration/test_gov_web_e2e.py`：更新指定的二次运行测试，确认网页结果为未变化 2、PDF 未请求、文档版本及 raw 文件未变化。
- 测试输出记录在 `test_gov_web_recheck.log`、`test_crawlers.log`、`test_gov_web_e2e.log`。

## 验收

- `make check T=tests/crawlers/test_gov_web_recheck.py`：9 passed。
- `make check T=tests/crawlers`：242 passed；其中包含 `CrawlerSuite` 合格测试。
- `make check T=tests/integration/test_gov_web_e2e.py`：12 passed。
- `git diff --check`：通过。

沙箱中的 `uv` 不可用，`make check` 按项目约定自动改用只读主仓库 `.venv` 执行。测试使用假实现、录制样本和本地 HTTP 测试服务器，没有访问外网。采集器只依赖 core 契约、模型及本任务逻辑；新测试用 `datumoj.testing` 假实现。

## 复核与风险

改动仅涉及任务 scope 文件。未独立完成 `other_family` 评审；没有进行真实政府站点联网验证。PDF 原位更新在成功抓取后 7 天内不会被发现，这是本任务设定周期带来的预期延迟。没有需要人类确认的操作。

未做联网验证，也未完成独立 `other_family` 评审；其余任务要求和验收均已核对。风险限于配置的 PDF 复查周期可能延迟发现文件更新。
