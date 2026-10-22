# DATUMOJ-TACHIBANA-207 完成说明

## 改动

- `src/datumoj/parsers/html_page.py`：新增页面日期块选择表，目前只登记 class `page_day01`。仅当命中元素的完整文字可由现有 `_parse_date` 解析时，才把它加入更新日候选；最终仍取全部候选中的最新日期。HTML 解析器版本从 `1` 升到 `2`。
- `src/datumoj/wiring.py`：`_GovWebContent.version` 动态组合 PDF 与 HTML 解析器版本，当前为 `pdf-1+html-2`，使流水线用分派入口版本记录的 gov-web 快照能识别 HTML 解析逻辑变化。
- `tests/parsers/test_html_page_dates.py`：覆盖两个大阪市日期、无效/含额外文字/未登记 class 的日期块、候选日期取最新、内阁府与总务省无日期、和历日期兼容，以及 HTML 版本变化带动分派器版本变化。
- `tests/parsers/test_html_page.py`：只将大阪市既有日期测试改为断言 `2026-08-26`。
- `results/DATUMOJ-TACHIBANA-207/tests.log`：记录验收命令输出。

## 版本影响

解析记录实际使用 `_GovWebContent.version`。此版本从原来的 `1` 变为 `pdf-1+html-2`，因此已有 gov-web 快照的旧解析记录会被视为旧版本；执行解析流程时会重新解析，无需重新抓取。该入口共用于 HTML 和 PDF，因此版本变化也会使已有 gov-web PDF 快照重新解析。集成测试验证了新的分派入口仍可完成 gov-web 解析与索引流程。

## 验收

以下命令均按任务中的验收命令执行并通过；测试使用录制样本和本地假服务器，没有联网测试。解析器整组测试包含 `HtmlPageParser` 的 `ParserSuite` 合格测试。

- `make check T=tests/parsers/test_html_page_dates.py`：10 passed。
- `make check T=tests/parsers`：73 passed。
- `make check T=tests/integration/test_gov_web_e2e.py`：12 passed。
- `git diff --check`：通过。

## 变更文件清单

- `src/datumoj/parsers/html_page.py`
- `src/datumoj/wiring.py`
- `tests/parsers/test_html_page.py`
- `tests/parsers/test_html_page_dates.py`
- `results/DATUMOJ-TACHIBANA-207/tests.log`
- `results/DATUMOJ-TACHIBANA-207/summary.md`

未改契约、夹具或依赖；无待人类确认事项。

跳过：线上抓取和任务验收范围外的全量测试。未核对：没有对外部已持久化目录实际执行 CLI 重解析；其版本触发行为由动态版本断言及 gov-web 集成测试覆盖。风险：共享分派器版本变化会同时触发 gov-web HTML 与 PDF 快照重解析。
