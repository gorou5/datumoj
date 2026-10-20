# DATUMOJ-IVY-305 完成说明（第 2 次尝试）

## 变更文件
- `src/datumoj/crawlers/estat_files.py`（新）：`EstatFilesCrawler`，`kind=estat_files`。
- `src/datumoj/parsers/estat_tables.py`（新）：`EstatTableParser`（`source_kind=estat_files`，`content_type="*"`），清单项模型 `TableEntry`，两种版式转换器。
- `sources/jp-estat-files.yaml`（新）：`scope.tables` 清单，3 项（人口推計 XLSX、経済センサス 东京 CSV、大阪 CSV）。
- `tests/crawlers/test_estat_files.py`（新）：67 个测试。
- `results/DATUMOJ-IVY-305/summary.md`（本文件）。

## 上次打回的两条
1. **依赖越界（直接 import `datumoj.text`）**：已去掉。解析器只 import `datumoj.core.*`（契约）、`datumoj.tabular`（301）、`datumoj.reference.areas`（302）；测试另用 `datumoj.core.registry`（104）。数值解析改用模块内的一个正则（`-?数字(.数字)?`），不再借用 003 的 `parse_number`。`datumoj.tabular` 内部用到 `datumoj.text` 属于 301 自己的实现，本任务没有直接 import。
2. **XLSX 不核对调查名**：现在两种版式都核对，不一致抛 `ParseError`，不再只发警告。
   - 规则：调查名要出现在**表头**里（标题区，不含注释）；或者全文包含代码里登记的该调查特有用语（`_SURVEY_MARKERS`）。
   - 人口推計的 XLSX 文件里**没有写调查名**（已查过全部单元格、docProps、workbook.xml，只有“補間補正人口”和注释里的“国勢調査による人口”）。所以给 `人口推計` 登记了特有用语 `補間補正人口`（国勢調査之间的补间修正人口，是人口推計的说法）。
   - 只查表头是第一次自测时发现的问题：XLSX 注释里有“国勢調査”，全文子串匹配会让 `survey=国勢調査` 误通过。现已测到。
   - 回归测试 `test_survey_mismatch_fails` 覆盖四种情况：XLSX 配 `平成28年経済センサス‐活動調査`、XLSX 配 `国勢調査`、CSV 配 `人口推計`、CSV 配 `令和3年経済センサス‐活動調査`，都抛 ParseError。

## 要求逐条
1. 插件 `kind=estat_files`，清单在 `scope.tables`，每项含 `stat_inf_id`、`survey`、`title`、`layout`、`expected_area_level`。`census_area_by_measure` 另须写 `area_code`（一个文件一个县，用来核对地区）；`wide_area_by_year` 不能写。清单不合法时 `plan` 抛 ValueError。
2. 下载后核对放在解析器里：采集器把清单项原样放进 `FetchTask.meta`，随快照存进 manifest；解析器按文件内容核对表题、调查名、地区，不一致抛 `ParseError`，流水线据此记为解析失败（ParseRecord failed）。另外核对快照 URL 的 statInfId 和清单项一致。
   - 没放在采集器的 `after_fetch` 里：契约规定那里抛异常会被当成整个来源这次运行失败，其余表也抓不成。
3. 版式只认 `wide_area_by_year`、`census_area_by_measure`（`Literal`）。以下情况都抛 ParseError，不猜：
   - 版式不在这两种里、meta 缺失；
   - 文件格式和版式不符（按内容 sniff，不看扩展名和响应头）；
   - 结构对不上：找不到年份行、单位、指标；第 1 列不是行号；列维度编码认不出；地区行认不出；
   - 值认不出。
4. 注记（`-` `…` `x` `秘匿` 等）进 `note`，`value=None`，不补零；`survey` 取清单值，且核对过文件。XLSX 年份列上的注标记（`1)`、`2)`）以 `注1)` 原样写进 note。
5. 测试里核对的数值：
   - XLSX 12 个（README 核对表全部）；
   - 东京 CSV 12 个（含 `-`）；
   - 大阪 CSV 10 个（含 `-`）。
   
   另有这些测试：地区不符、调查名不符、表题不符、未知版式、版式与格式错配、meta 缺失、URL 不符、值认不出、2015 年重复值冲突。
6. `wide_area_by_year`：
   - 4 张工作表；
   - 2015 年按 (指标, 地区, 年) 去重，保留先出现的工作表。locator 是 `sheet=…;cell=…`，记录来源工作表；ParseResult.warnings 写明哪两张表重复、多少个地区。两边值不同就报错。
   - 产出 2016 个观测值（2 指标 × 48 地区 × 21 年）。
   - 时间取表题“各年10月1日現在”，即 time_start = time_end = 当年 10-01。
   
   `census_area_by_measure`：
   - 表头定位：第一行带 `ti.xxxxx` 的数据行之前的 6 行是列维度编码与名称，实测表头 13 行；
   - 维度：dims 含产业名、产业码、产业层级、经营组织名、经营组织码；indicator_code 是测度码；“１事業所当たり従業者数”列不分经营组织；
   - 时间取文件里调查名中的“平成28年”，即 2016-01-01〜2016-12-31，time_label 保留“平成28年”；
   - 每个文件 1934 行 × 25 列 = 48350 个观测值。
7. 东京的 statInfId 下载到大阪文件（两者下载名都是 `02000.csv`）时，报 `文件内容的地区是 27000 大阪府，清单要求 13000`。测试中两个 CSV 的响应头都按录制时写成 UTF-8，实际按 cp932 读出（`東京都`、`大阪府`、日文指标名都核对了）。XLSX 下载 URL 用 `fileKind=4`。
8. 继承 `CrawlerSuite`、`ParserSuite` 并全部通过。用 `drive_crawler` 驱动采集，parse 的输入取自假 manifest 的 meta 和假 raw；观测值经 `ParseResult.observations` 交出。

## 验收
- `make check T=tests/crawlers/test_estat_files.py` → `67 passed in 8.11s`
- 全量 `.venv/bin/pytest -q` → `822 passed`（确认插件发现、注册表加载新 yaml 都不出错）。

## 跳过 / 未核对 / 风险
- 人口推計 XLSX 的调查名只能靠特有用语“補間補正人口”确认，文件里没有调查名。更可靠的办法是抓同一 statInfId 的数据集页面核对，但没有离线样本，本次没做。新增别的不写调查名的表时，要先在真实文件里核实用语，再加进 `_SURVEY_MARKERS`。
- `expected_area_level` 目前只接受 `prefecture`：只有都道府県样本，市区町村级的地区层级编码没有核实过。
- 事業所数一列在文件里没有单位，`unit` 留空，没有自己补“事業所”。
- 没有新增依赖，没有改契约、fixtures、`docs/`。
