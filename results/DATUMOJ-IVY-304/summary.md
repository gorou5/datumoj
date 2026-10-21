# DATUMOJ-IVY-304 完成说明

实现了统计 Dashboard 指标目录全量采集、精选指标数据采集、分批和 Observation 转换。

## 要求核对

1. `stat_dashboard` 采集器请求不带 `Category` 的完整指标目录；数据请求只使用 `scope.indicators`。
2. `plan_batches` 是纯函数，分别按每批 5 个指标和 50 个地区拆分，并覆盖正好到边界、超出边界和空输入。
3. 解析器把目录转成 `Dataset`，把数据行转成 `Observation`；地区码通过 DATUMOJ-IVY-302 归一化，支持年、年度、半年、季度、月、周和日时间码，单位使用响应目录标签。
4. 同一指标/地区批次再次采集时使用其最近成功快照日期生成 `modifiedFrom`；测试验证带参数的请求仍完整解析响应。
5. 来源清单包含 10 个从录制目录/数据样本选出的指标。
6. 测试逐条核对 b1、b2、b3 中每个返回值，并检查地区、时间、单位、调查名以及部分地区缺失不补零。
7. `obs_id` 由响应行内容稳定生成；同一响应中的完全重复行合并、冲突记录报错。跨快照解析不去重，响应仅因抓取时间变化仍完整输出全部行。
8. 新增 30 条人工目录样本；目录请求测试确认没有 `Category` 参数，目录解析输出与样本逐条对应。
9. 采集器和解析器分别继承 `CrawlerSuite`、`ParserSuite`；采集器测试由 `drive_crawler` 驱动。

## 验收

执行 `make check T=tests/crawlers/test_stat_dashboard.py`，结果 21 passed。完整终端输出见 [test-output.log](test-output.log)。

变更文件清单见 [files.txt](files.txt)。未修改契约或任务范围外文件。

跳过/未核对：未联网核实服务条款链接或向 Dashboard 发请求；测试仅使用离线录制和人工样本。风险：实时 API 行为变化不在本次离线验收覆盖范围内。
