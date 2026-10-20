# DATUMOJ-IVY-301 完成说明

已实现内容识别、CSV/XLSX 读取、ZIP 递归、多行表头及注记识别。仅修改任务 scope，无新增依赖、无联网、无 git commit。

## 改动到达范围

- 实现与公开导出：`src/datumoj/tabular/__init__.py`，原来为空；仓库尚无调用方需要迁移。
- 测试：`tests/tabular/test_readers.py`，46 个离线用例。
- 样本：只读现有 `tests/fixtures/tabular/`；12 项 SHA-256 全部匹配，无新增或修改样本。
- 配置、其他模块导出及契约：无需修改。复用契约 `format_locator`，以及任务明确要求的 IVY-003 共用文本工具 `decode_bytes` / `parse_number`。
- 本任务是表格读取工具，不实现插件协议；当前 checkout 无 `core/contracts.py` 或可继承的表格合格测试，不需要另立或修改契约。

## 逐项核对

| 要求 | 实现与验证 |
| --- | --- |
| 1 内容 sniff | OLE 魔数标记 `xls`；ZIP 中央目录同时含 `[Content_Types].xml`、`xl/workbook.xml` 才标记 `xlsx`；否则 `zip`。文本按内容解码；不可识别内容标记 `unsupported`。覆盖 XLSX、CSV、普通 ZIP、Content_Types 诱饵和损坏归档。API 不接收扩展名、Content-Type 或声明编码。 |
| 2 CSV | 复用 `decode_bytes`；支持逗号、制表符、分号、竖线，含不等长标题/注释行回退。保留空字段、空白、文本数字和引号内换行；两个真实 cp932 CSV 全字段逐一比对原件独立解码结果。 |
| 3 XLSX | `read_only=True`、`data_only=True`、`keep_links=False`，缓存公式读取既有结果，无缓存返回空字符串，不执行公式。逐工作表记录定位；单独读取 XML 合并范围，保留原始空单元格。测试覆盖公式缓存、多工作表、日期、合并表头及伪造 dimension。 |
| 4 ZIP | UTF-8 标志优先，否则从 cp437 还原文件名字节并尝试 cp932。按成员内容递归，不落盘解压；嵌套 ZIP 与 XLSX 内部成员共享数量/声明解压大小预算。CRC、损坏归档及不安全路径错误会抛出。 |
| 5 RawTable | `rows` 为不可变单元格行元组；每格 `row` / `column` / `raw` / `locator`，行列从 1 开始。表保存 `sheet`、`member`、`merged_ranges`。locator 经契约函数转义 `%`、`;`、`=`，测试验证可还原。 |
| 6 表头与注记 | `detect_header` 返回 `header_rows`、`columns`、`note_rows`、`data_start`；识别最多 4 行表头并以 ` / ` 拼接。合并单元格、未真正合并的稀疏表头、逐格重复表头均覆盖。识别表前/表尾注记及续行，保留原始表不变。 |
| 7 全覆盖与 ZIP 炸弹 | 46 个用例覆盖上述功能及嵌套共享预算、深度、CRC、危险路径、单元格数限制。三种既有炸弹在打开任何成员之前被拒绝，另有低于大小上限的高压缩比测试。 |
| 8 H1 真实/合成样本 | 两份真实 e-Stat CSV、两份真实 e-Stat XLSX 和 BODIK XLSX 全部读取；真实表头分别覆盖 2、4 行。合成 XLSX 覆盖真正的 3 行合并表头、`x` / `X` / `秘匿` / `△` / `▲` / `資料：` 等。真实样本的 `-`、`…`、注行及尾随空白均断言。 |

## API 与边界

- `read_csv(bytes, *, member=None) -> RawTable`；`read_xlsx(bytes, *, member=None) -> list[RawTable]`；`read_zip(bytes) -> list[RawTable]`。
- CSV locator 包含 `cell` 和 `row`；XLSX locator 包含 `sheet` 和 `cell`，归档来源另带 `member`。嵌套路径用 `外层成员!/内层路径` 表示。CSV 行号为逻辑记录号，引号内换行不增加记录号。
- ZIP 上限：累计 1000 个成员（含目录和 XLSX 内部文件）、累计声明解压大小 64 MiB、单成员压缩比 200:1、最多 8 层 ZIP；超限抛 `ArchiveLimitError`。成员读取还受声明大小约束并核对长度，由 zipfile 校验 CRC。每个表最多 1,000,000 个单元格；XLSX 忽略不可信 dimension 提示。
- `raw` 不做数字或缺失值转换；`-`、`…`、保密标记不会补零。XLSX 使用 openpyxl 读取到的值转换为字符串，空值为 `""`，不模拟 Excel 的格式化显示。
- `sniff` 对旧 XLS 返回 `xls`；归档中遇到旧 XLS 抛出明确的 `NotImplementedError`。归档内 XML/JSON/不可解码二进制等 `unsupported` 成员跳过，不属于表格解析范围。

## 验证与审查状态

- 指定验收原文：`make check T=tests/tabular/`，最终 **46 passed in 1.79s**；完整输出见 `acceptance.log`。make 按既有规则使用主仓库只读 `.venv` 回退，未安装依赖。
- `ruff check src/datumoj/tabular tests/tabular`、`ruff format --check src/datumoj/tabular tests/tabular` 均通过；`git diff --check` 通过。
- 自查三个审查项：不信扩展名/Content-Type；ZIP 有解压与递归上限；全部变更均在 scope 内。任务材料无“评审中的高置信度质疑”清单。
- `other_family` 交叉审查和 human sampled 由调度器后续执行，本次没有代为宣称通过。

未做统计口径映射、XLS 读取、其他开发线集成或全仓测试。表头推断是启发式，纯数字年份列名等版式仍需下游按明确布局验证；只读合并范围读取使用 openpyxl 的 `_worksheet_path`，升级依赖时应复核。
