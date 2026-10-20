# DATUMOJ-IVY-202 完成说明

实现 `LawXMLParser` 插件：接受 e-Gov `law_data` JSON 响应或 XML 字节，采用标准库 Expat 建树并拒绝 DTD/实体声明；原始输入及解码后的 XML 均限制为 16 MiB。按法令层级生成 title path，普通条文按条切分，超过 2000 字时按段落、长段内按项切分。附则和别表分别生成 `suppl=...` 与 `table=...` locator。`law_id`、`asof`、未施行修改来自 `meta`；`asof` 同时写入 Document metadata 与所有 Chunk 的 `effective_at`，缺失时不填日期。条号使用 `article_number`，范围条标题回退到 XML `Num`。

## 核对

- 带章节、条の二、附则、别表、长条切分、恶意实体 XML、日期/未施行修改 metadata、跨快照稳定 chunk ID 均有测试。
- 测试类继承 `datumoj.testing.contract_suites.ParserSuite`；测试读取既有离线 fixtures，未修改 fixtures，也没有联网调用。
- 修改仅限本任务 scope；未改契约、配置、调用方或导出。

## 变更文件

- `src/datumoj/parsers/xml_law.py`
- `tests/parsers/test_xml_law.py`
- `results/DATUMOJ-IVY-202/test.log`
- `results/DATUMOJ-IVY-202/summary.md`

## 验收

命令：`make check T=tests/parsers/test_xml_law.py`  
结果：通过，10 passed。沙箱内 `uv` 不可用，Makefile 按现有规则使用只读 `.venv`。完整输出见 `test.log`。

## 尚未完成的外部验收

任务定义的 `other_family` 复核及 human sampled 尚未执行；当前报告为实现者的 scope、契约套件和测试覆盖核对。

跳过了非 `AppdxTable` 附表类型与逐条修订影响标记（任务未要求后者）。未核对外部 reviewer/human sampled；超过 2000 字但没有 `Paragraph` 节点，或超长段落没有 `Item` 节点时，正文会保留为一个 chunk。
