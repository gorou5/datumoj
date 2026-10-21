# DATUMOJ-IVY-204 完成说明

## 改动

- 在 `LawXMLParser.parse` 入口识别 `law_list` 和 `law_revisions`，返回没有 documents/chunks 的 `ParseResult`，并添加 `非正文快照（role=…），跳过` 警告。
- `law_data`、缺失 `meta`、缺失 `role` 继续走原正文解析路径；原解析实现未改动。
- 新增测试，使用真实列表、修订历史和 law_data 样本覆盖跳过、正常解析及缺省 role 的错误行为。

## 验收

- `make check T=tests/parsers/test_xml_law_roles.py`：5 passed。
- `make check T=tests/parsers/test_xml_law.py`：10 passed，原 IVY-202 解析测试通过。
- `git diff --check`：通过。
- 两条 `make check` 因沙箱内 `uv` 不可用，按 Makefile 提示使用现有 `.venv` 运行；完整输出见 [acceptance.log](acceptance.log)。

## 变更文件

- `src/datumoj/parsers/xml_law.py`
- `tests/parsers/test_xml_law_roles.py`
- `results/DATUMOJ-IVY-204/acceptance.log`
- `results/DATUMOJ-IVY-204/summary.md`

## 评审核对

- 列表和修订历史快照返回空产物及警告，不抛解析异常；测试使用真实样本验证。
- `law_data` 分支保留原逻辑；新样本解析测试通过，原有 10 项 IVY-202 测试全部通过。
- 修改仅涉及任务 scope 中的解析器、指定测试和结果文件。
- 任务正文没有“评审中的高置信度质疑”一节。

跳过：未运行全仓测试。未核对 scope 外的流水线集成行为；本任务未修改流水线。风险：无已知。
