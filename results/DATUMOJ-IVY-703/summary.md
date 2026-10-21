# DATUMOJ-IVY-703 完成说明

## 实现

- 新增目录题库 10 道，命中 e-Gov CKAN fixture 中的 10 个具体数据集；中文或英文题 5 道，每题都有日文检索改写。
- 新增数值题 10 道：统计 Dashboard 原始 JSON 9 道、e-Stat 东京 CSV 1 道。SQL 均查询 `observations` 视图，题目备注给出原始文件和 JSON 记录路径或 CSV 单元格。
- 数值测试直接从原始 fixture 读取数值并与 `equals` 比对；Dashboard 记录同时核对指标、地区、时间和 `@stat`，CSV 核对 `I977` 的产业码、指标码和值。
- e-Stat 东京 ICT 従業者数题按 `indicator_code = 'jsyu102A.0001'` 精确筛选，并按 `dims` 限定 `sanD1.0963`。测试用获准的 `FakeObservationStore` 执行全部 10 道题的 SQL；e-Stat 回归样例含同一行业的男女观测值，断言查询只返回一行且等于原表值。

## 要求与复核

1. 目录题共 10 道，期望 `doc_key` 对应 fixture 中的具体 CKAN 数据集；5 道中文或英文题带日文改写。
2. 数值题共 10 道，SQL 查询 `observations`；`equals` 来自原始 JSON-stat 响应或 e-Stat CSV 的记录/单元格。
3. 测试加载完整题库，检查题目数量、CKAN 数据集 ID 和来源文件，逐题读取数值原表并对照 `equals`；所有题目 SQL 在假观测库中执行，断言返回单行和期望值。
4. 退回意见中的模糊 `従業者数` 匹配已改为精确指标码，测试加入男性、女性同业观测值作为区分样例。调查来源按原始 Dashboard 记录 `@stat` 校验；就业题文案已改为该记录所属的社会・人口統計体系。

## 验收

执行 `make check T=tests/eval/test_questions_stats.py`：5 passed。沙箱中 uv 不可用，Makefile 自动回退到既有只读虚拟环境；详细输出见 `acceptance.log`。

## 未覆盖与风险

- 退回意见要求在本测试中直接导入 `EstatTableParser` 和 `DuckDBObservationStore` 执行 SQL；该任务只依赖 DATUMOJ-IVY-701，而 DATUMOJ 项目的导入边界禁止测试导入非依赖任务的实现模块（解析器和存储分别来自其他任务）。因此本次使用允许的假观测库执行 SQL，真实解析器与 DuckDB 存储之间的集成仍未核对。
- 本 worktree 中原先没有 `catalog.yaml`、`stats.yaml` 或对应测试文件；本次依据题目指定的 fixture 重建题库，未能逐字保留上次被打回提交中其余 19 道题的原文。

跳过：未导入非依赖任务的解析器与 DuckDB 存储实现，未访问网络，也未改动 fixture 或 `src/`。未核对：真实解析结果经 DuckDB 执行全部题目 SQL 的集成链路。风险：该集成点受项目导入边界限制，需由允许访问相应依赖实现的任务或评审环境补核。
