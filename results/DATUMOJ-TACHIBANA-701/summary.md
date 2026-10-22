# DATUMOJ-TACHIBANA-701 完成说明

## 变更

- `eval/questions/gov_web.yaml`：新增 12 道日、英、中题，覆盖内阁府/总务省/大阪市网页、内阁府 PDF 页码、更新日与 `no_data_date`、跨站点比较，以及不替用户判断补助资格。所有来源 ID 均为 `jp-gov-web`；合成页使用任务指定的 `example.invalid` URL。
- `eval/questions/tachibana_gaps.yaml`：新增 8 道题，包括来源正文注入 3 道、e-Stat 常见词零命中 2 道、目录检索 2 道和目录出处还原 1 道。
- `tests/eval/test_questions_tachibana.py`：检查题库加载、题数与 ID 唯一；逐题按 URL 构造快照并调用 HTML/PDF 解析器验证 doc key 和 locator；验证合成日期页提取 `2025-04-01`、无更新日页面不伪造日期；用 e-Stat 表解析器、目录存储和 `cite` 验证两个表可检索且出处可还原。
- `acceptance.log`：保存最终版本的两条验收输出。

## 方案与核对

复用了已合并的 201/202 解析器，key 和 locator 均由 fixture 原始 URL 与实际解析结果核验。大阪市页面的 h4 不进入 locator，因此题目定位到解析器实际生成的 h3 路径。e-Stat 题使用已有 `statInfId` 和原始 fixture，不新增样本或依赖。

新增的个人补助资格题以检索到官方适用条件作为机器可判期望，并在题目说明中要求拒绝代用户作资格判断。现有严格评测模型没有独立的拒答断言字段，因此该回答行为仍需人工审阅；没有扩展评测框架。

## 验收

- `make check T=tests/eval/test_questions_tachibana.py`：通过，4 passed。
- `make check T=tests/eval`：通过，73 passed。
- 两条命令都由 `make check` 回退到主仓库 `.venv` 执行；日志第一行记录了该情况。
- 未运行 903 门槛，按任务要求留给集成任务。

## 待人工复核 / 风险

- 任务要求的人审仍待完成，重点复核 fixture 依据、检索预期和个人资格拒答表达。
- 个人资格拒答的生成结果不能由当前题目 schema 单独量化；其他检索 key、locator、日期与出处还原已有离线测试覆盖。
- 未发现契约、依赖或 scope 变更；未修改 fixture。
