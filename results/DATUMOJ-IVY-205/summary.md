# DATUMOJ-IVY-205 完成说明

## 修改

- 在 [xml_law.py](../../src/datumoj/parsers/xml_law.py) 中为 `MainProvision` 的直接 `Paragraph` 增加分块：使用 `Paragraph@Num` 作为 `para=N`，缺少时按顺序编号；多项时标题路径追加“第N項”，单项保留法令名。条文、附则及别表分支未改。
- 新增 [test_xml_law_paragraph_only.py](../../tests/parsers/test_xml_law_paragraph_only.py)：覆盖三份真实样本（分别 1、3、4 块）、`para=N`、正文开头、稳定 `chunk_id` 和标题路径；人工 XML 覆盖条文与项混排及缺少 `Num`；空本则与空附则验证仍报 `ParseError`。

## 验收

- `make check T=tests/parsers/test_xml_law_paragraph_only.py`：5 passed。
- `make check T=tests/parsers/test_xml_law.py`：10 passed。
- `make check T=tests/parsers/test_xml_law_roles.py`：5 passed。
- `git diff --check`：通过。
- 测试输出记录在 [acceptance.log](acceptance.log)。沙箱中 `uv` 不可用，`make check` 按项目约定使用已有 `.venv`。

## 评审核对

- 只有“项”的三份真实法令均按项输出，locator 为 `para=N`；测试核对块数、正文开头和 `chunk_id`。
- 有“条”的法令分支没有改动，原 XML 法令与角色测试均通过。
- 附则、别表分支没有改动；原解析测试（含附则、别表样本）通过。
- 任务列出的原 IVY-202/204 相关解析验收均通过。
- 修改仅涉及任务 scope 内的解析器、指定新测试和本目录的结果文件；受保护 fixtures 未改。

## 文件清单

- `src/datumoj/parsers/xml_law.py`
- `tests/parsers/test_xml_law_paragraph_only.py`
- `results/DATUMOJ-IVY-205/acceptance.log`
- `results/DATUMOJ-IVY-205/summary.md`

未跑全仓测试；本次只核对任务列出的验收项。风险是全仓其他测试未覆盖。
