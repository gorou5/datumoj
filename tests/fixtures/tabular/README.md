# tabular：表格读取离线样本

供 DATUMOJ-IVY-301（表格读取：编码嗅探、cp932、ZIP、合并表头、注记）离线测试使用。

- 真实样本有两类：**旧项目证据**（`digital-assets/plugin-prototype/evidence/`（旧项目工作区，下文路径都相对于 `digital-assets/`），旧项目当时原样保存的响应体和响应头）和**本次录制**（2026-10-20，经 e-Gov 目录找到的 e-Stat 文件，User-Agent `DATUMOJ-fixture-recorder/0.1 (+https://github.com/gorou5/datumoj)`，≤1 次/秒，遵守 robots.txt）。
- 文件名带 `.synthetic` 的是人工构造的，构造方法见下文；构造脚本没有放进仓库，方法写在本文件里，可以照着重现。
- 所有真实文件都是原始字节（本次录制的只去掉了传输层 `Content-Encoding: gzip`）。每个文件的来源 URL、状态码、响应头、旧项目路径、SHA-256 在 `_responses.json`；完整性校验用 `SHA256SUMS`（覆盖除 README.md 外的全部文件）。
- 合计约 1.8 MB，单个文件都小于 1 MB。

## 许可与署名

| 文件 | 许可 | 署名 |
|---|---|---|
| `estat_ecensus2016_t2_osaka.csv`、`estat_eisei_gyosei_r6_t10.csv` | e-Stat 利用規約（政府標準利用規約第2.0版准拠，与 CC BY 4.0 兼容；旧项目记录为 “Japanese government statistics public data terms; source attribution required”） | 出典：政府統計の総合窓口(e-Stat)（https://www.e-stat.go.jp/）、総務省・経済産業省「平成28年経済センサス‐活動調査」第2表（大阪府）；厚生労働省「令和６年度衛生行政報告例」第10表 |
| `estat_chiho_kyoikuhi_h28_t7-1.xlsx`、`estat_hogosha_r2_t1-1.xlsx` | e-Gov 目录中资源许可 `cc-by`；e-Stat 利用規約 | 出典：政府統計の総合窓口(e-Stat)、文部科学省「地方教育費調査」平成28年度 第7表；厚生労働省「被保護者調査」2020年度 第1表（2-1） |
| `bodik_mukou_population.xlsx` | CC BY 4.0（BODIK 目录 `license_id=cc-by-40-intl`） | 出典：向日市「地域・年齢別人口」（BODIK ODCS，https://data.bodik.jp/dataset/000d6f71-c569-442c-bf8c-98ddc2c375a3） |
| `ksj_s12_mixed_encoding.synthetic.zip` | 含 CC BY 4.0 原始数据的 2 条记录（旧项目记录 S12 许可为 `CC-BY-4.0`；建议对照 https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-S12-2024.html 再确认） | 「国土数値情報（駅別乗降客数データ）S12-25」（国土交通省）を加工して作成 |
| 其他 `.synthetic` 文件 | 本项目自制，随仓库许可 | — |

内容检查：所有样本都是统计汇总或政府公开的地域统计，不含个人信息；XLSX 的 `docProps/core.xml` 作者字段为空或 `Administrator`。

## 文件一览

| 文件名 | 来源（URL / 旧项目路径） | HTTP 状态 | 抓取时间 | 大小（字节） | 编码 | 用途（测试点） |
|---|---|---|---|---|---|---|
| `estat_ecensus2016_t2_osaka.csv` | `https://www.e-stat.go.jp/stat-search/file-download?statInfId=000031721271&fileKind=1`；旧项目 `plugin-prototype/evidence/277d8706-e93e-463b-bfb7-84c776201dd5/trade-area-2/official.csv`（响应头 `…/http-2.json`） | 200 | 2026-10-16T01:05:31+09:00（响应头 Date，旧项目抓取） | 328049 | **cp932**，但响应头 `Content-Type: text/csv;charset=UTF-8`、`Content-Disposition: attachment; filename*=UTF-8''02000.csv` | **坑 1**：声明 UTF-8 实际 cp932，必须按字节嗅探/校验，不信响应头。另有 13 行表头（标题、英文标题、`(注1)(注2)`、代码行、两级列名），数值里不带引号的 `-` |
| `estat_eisei_gyosei_r6_t10.csv` | `https://www.e-stat.go.jp/stat-search/file-download?statInfId=000040359178&fileKind=1`；旧项目 `plugin-prototype/evidence/4d436e64-4012-4790-88dd-47151ca30221/trade-area-5/official.csv`（响应头 `…/http-5.json`） | 200 | 2026-10-16T22:49:52+09:00（同上） | 8126 | **cp932**，响应头同样写 `charset=UTF-8` | **坑 1**（小样本版）；**坑 4**（CSV 版）：表头前有标题和 `注：１）…` 多行注记，4 行表头（上层列名逐格重复，相当于展开后的合并单元格），`-` 注记，表中途出现“指定都市（再掲）”分段行（下接城市行） |
| `estat_chiho_kyoikuhi_h28_t7-1.xlsx` | `https://www.e-stat.go.jp/stat-search/file-download?statInfId=000031591672&fileKind=0`（e-Gov 数据集 `3e9f689f-0181-4fa4-ac2f-59f6a66e2456`，资源 `dfc40c27-3d9f-4c79-b9d5-553809f866a1`） | 200 | 2026-10-20T21:06:05+09:00 | 12239 | XLSX；响应头 `application/octet-stream`，文件名 `8ksqfy2015_0701.xlsx` | **坑 4**（真实 XLSX）：表头前 4 行标题/单位/注，注释行解释 `「-」は計数がない場合，「…」は計数があり得ない場合`，2 行表头（上层只在首格写值、其余留空；**没有真正的合并单元格**），单元格含 `-`、`…`，行名带尾随空格 |
| `estat_hogosha_r2_t1-1.xlsx` | `https://www.e-stat.go.jp/stat-search/file-download?statInfId=000032172576&fileKind=0`（e-Gov 数据集 `684e5975-0ca4-40a7-a0a3-5a78efb60295`，资源 `873b885a-ba72-483c-919a-753b3b78c427`） | 200 | 2026-10-20T21:05:54+09:00 | 23361 | XLSX；响应头 `application/octet-stream`，文件名 `2020_hou0001a.xlsx` | **坑 4**（真实 XLSX）：`注：…` 在表头之上，4 行表头（上层值逐格重复），`・` 注记（中核市“郡部”列），两列行标签（区分 + 名称） |
| `bodik_mukou_population.xlsx` | `https://data.bodik.jp/dataset/000d6f71-c569-442c-bf8c-98ddc2c375a3/resource/50f27e3f-3ca4-40cc-9927-ed685e345db9/download/262081_population.xlsx`；旧项目 `plugin-prototype/evidence/b38f059e-1297-44df-90b0-3c152414b66c/file-20-xlsx/raw/evidence/raw/bodik.file.xlsx-3.xlsx`（响应头 `…/http-22.json`） | 200 | 2026-10-10T18:40:51+09:00（同上） | 370834 | XLSX，`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | **坑 3**：真正的 XLSX（单表、单行表头、日期单元格），与下面的普通 ZIP 对照 |
| `ksj_s12_mixed_encoding.synthetic.zip` | 按旧项目 `plugin-prototype/evidence/277d8706-e93e-463b-bfb7-84c776201dd5/trade-area-6/official.zip`（`https://nlftp.mlit.go.jp/ksj/gml/data/S12/S12-25/S12-25_GML.zip`，6913743 字节，超过 5MB 上限）的结构构造 | — | 构造于 2026-10-20 | 3422 | 成员混合：cp932 与 UTF-8 | **坑 2**：同一 ZIP 内 `Shift-JIS/` 与 `UTF-8/` 两套同名成员，内容编码不同；外加一对**文件名**编码不同的成员；**坑 3**：普通 ZIP（不是 XLSX） |
| `zip_not_xlsx_content_types.synthetic.zip` | 人工构造 | — | 构造于 2026-10-20 | 434 | ZIP（成员 UTF-8） | **坑 3**：带 `[Content_Types].xml` 但没有 `xl/workbook.xml` 的普通 ZIP，只看 `PK` 头或只看 `[Content_Types].xml` 都会误判成 XLSX |
| `stat_merged_header_notes.synthetic.xlsx` | 人工构造（openpyxl） | — | 构造于 2026-10-20 | 6202 | XLSX | **坑 4**：真正的合并单元格表头（3 行：`A3:A5`、`B3:E3`、`F3:I3`、`J3:J5`、`B4:B5`、`C4:E4`、`F4:F5`、`G4:I4`），表尾 `注：１）…`、`２）…`、`資料：…`、`出典：…`，单元格注记 `-`、`－`、`…`、`x`、`X`、`秘匿`、`△0.8`、`▲2.5`、`*`、文本数字 `" 7,500"`、`"0.0"` |
| `zip_bomb_high_ratio.synthetic.zip` | 人工构造 | — | 构造于 2026-10-20 | 203958 | ZIP | **坑 5**：1 个成员 `zeros.bin`，声明并实际解压为 209715200 字节（200 MiB 全零），压缩比约 1000:1 |
| `zip_bomb_many_members.synthetic.zip` | 人工构造 | — | 构造于 2026-10-20 | 900022 | ZIP | **坑 5**：10000 个空成员（`m/00000`…`m/09999`），测成员数上限 |
| `zip_bomb_declared_size.synthetic.zip` | 人工构造 | — | 构造于 2026-10-20 | 126 | ZIP | **坑 5**：1 个成员 `huge.csv`，本地头和中央目录里声明的解压大小都改成 4294967280（约 4 GiB），实际只有 10 字节（CRC 与声明不一致，读取会报错）；测“先看声明大小再决定是否解压” |

## 构造方法

所有 ZIP 成员时间戳固定为 2026-10-20 00:00:00。

- **`ksj_s12_mixed_encoding.synthetic.zip`**：复制原 S12-25 ZIP 的目录结构（`S12-25_GML/`、`S12-25_GML/Shift-JIS/`、`S12-25_GML/UTF-8/`，`create_system=3`，DEFLATE）。
  - `S12-25_GML/{Shift-JIS,UTF-8}/S12-25_NumberOfPassengers.geojson`：取原 ZIP 对应成员的前 5 行（`{`、`type`、`name`、`crs`、`"features": [`）和前 2 个 feature 行（二月田、古島），去掉末尾逗号，补 `]` `}`。两个成员内容相同，只是编码分别为 cp932 与 UTF-8，与原件一致。
  - `*.prj`：原件 145 字节原样复制。
  - `S12-25_GML/KS-META-S12-25.xml`：仿原件写了一个 `encoding="shift_jis"` 的最小 XML（cp932 编码，只有标题）。
  - **扩展（原件里没有）**：原 S12 的成员文件名全是 ASCII。为了覆盖“文件名编码混用”，另加两个成员：`S12-25_GML/Shift-JIS/駅別乗降客数_説明.txt`，文件名用 cp932 字节写入、**不设** UTF-8 标志（bit 11=0），内容 cp932；`S12-25_GML/UTF-8/駅別乗降客数_説明.txt`，文件名 UTF-8、设 UTF-8 标志（bit 11=1），内容 UTF-8。Python `zipfile` 读第一个时会按 cp437 解码出乱码 `ëwò╩Åµì~ïqÉö_Éαû╛.txt`，需要 `name.encode("cp437").decode("cp932")` 才能还原。
- **`zip_not_xlsx_content_types.synthetic.zip`**：两个成员：`[Content_Types].xml`（只声明 csv 的 Default）和 `data/sample.csv`（UTF-8，3 行，含 `-`）。
- **`stat_merged_header_notes.synthetic.xlsx`**：openpyxl 生成，工作表名 `第3表`。数据（甲市、乙町等）全部是编造的，不对应任何实际统计；版式模仿 e-Stat 统计表（标题行、单位行、3 行合并表头、表尾注记）。作者字段写 `DATUMOJ`。
- **`zip_bomb_high_ratio.synthetic.zip`**：`zipfile.ZIP_DEFLATED`、`compresslevel=9`，流式写入 200 × 1 MiB 的零字节。
- **`zip_bomb_many_members.synthetic.zip`**：`ZIP_STORED`，10000 个 0 字节成员。
- **`zip_bomb_declared_size.synthetic.zip`**：先正常写入 1 个 10 字节成员，再把本地文件头偏移 22 处和中央目录头偏移 24 处的 4 字节 “uncompressed size” 改成 `0xFFFFFFF0`。

## 和要求 / 文档不一致之处

1. **合并表头 + 注记的真实 XLSX 没找到**：旧项目证据里的统计 XLSX（2024 年经济センサス 4-1A/4-1B）分别为 6.27MB、6.83MB，超过 5MB 上限，而且没有合并单元格、没有 `x`/`…`/`△`。本次从 e-Gov 目录另找了两个小的 e-Stat XLSX（`estat_chiho_kyoikuhi_*`、`estat_hogosha_*`），它们有多行表头、`注`、`-`、`…`、`・`，但用的是“首格写值、其余留空”或“逐格重复”，**没有真正的合并单元格**，也没有 `x`/`秘匿`/`△` 和表尾 `資料：`。所以真正的合并单元格和这些注记只能由 `stat_merged_header_notes.synthetic.xlsx` 覆盖。
2. 寻找过程中有 2 个 e-Gov 登记的 XLSX 链接已经失效（福岡市漁業センサス 404；国税庁民間給与実態統計 302→404），另有 1 个 e-Stat XLSX（`statInfId=000032167506`，民間非営利団体実態調査）下载成功但没有所需特征，未收录。
3. 旧项目记录的 S12 “Shift-JIS 与 UTF-8 混用”只发生在**成员内容**（两个目录各一套），文件名是 ASCII；文件名编码混用是本样本额外加的，见“构造方法”。
4. `estat_ecensus2016_t2_osaka.csv` 在旧项目证据里有多份同哈希副本（`3c4f0d6e-…`、`277d8706-…` 等），这里取 `277d8706-…` 那份及其响应头。
