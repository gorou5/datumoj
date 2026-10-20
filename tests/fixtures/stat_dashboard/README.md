# 统计 Dashboard 样本（DATUMOJ-IVY-304）

统计ダッシュボード（`https://dashboard.e-stat.go.jp/api/1.0/`）的真实响应，供指标目录解析、分批 `getData` 转换和增量（`modifiedFrom`）测试离线使用。

## 许可与署名

- 出典：総務省統計局「統計ダッシュボード」（https://dashboard.e-stat.go.jp/）。各系列的原始调查见响应中的 `@statName` / `STAT_NAME`（国勢調査、人口推計、人口動態調査、社会・人口統計体系、県民経済計算等）。
- 按「政府標準利用規約（第2.0版）」（与 CC BY 4.0 兼容）利用和再分发，使用时须注明上述出典。本目录文件为原样保存的 API 响应，未加工。
- API 无需 Key，URL 里不含任何凭据。

## 录制方式

- 2026-10-20 录制；User-Agent `DATUMOJ-fixture-recorder/0.1 (+https://github.com/gorou5/datumoj)`；请求间隔 ≥1.2 秒。
- `dashboard.e-stat.go.jp/robots.txt` 返回 404（无限制）。
- 文件内容 = HTTP 响应体字节（服务器以 `Content-Encoding: gzip` 传输，保存的是解压后的响应体，未重新编码、未格式化）。编码均为 UTF-8（响应头 `application/json;charset=UTF-8`，实际字节也是 UTF-8，一致）。

## 文件清单

| 文件名 | 请求 URL | HTTP 状态 | 抓取时间 | 大小（字节） | 编码 | 用途 |
| --- | --- | --- | --- | --- | --- | --- |
| `dashboard-metadata-public.json` | 旧项目样本（见下）；原始请求为 `getIndicatorInfo?Lang=JP&IndicatorCode=0201010000000010000` 与 `getRegionInfo?Lang=JP&RegionCode=13000,27000` | 200 / 200 | 2026-10-01T13:11:20+09:00 | 6686 | UTF-8 | 旧项目样本：指标与地区元数据（**两个响应包在一个对象里，是旧项目重新序列化的包装，不是单次原始响应**） |
| `dashboard-population-public.json` | 旧项目样本：`JsonStat/getData?Lang=JP&IndicatorCode=0201010000000010000&RegionCode=13000,27000&TimeFrom=2022CY00&TimeTo=2022CY00&Cycle=3&RegionalRank=3&IsSeasonalAdjustment=1&MetaGetFlg=Y&SectionHeaderFlg=1` | 200 | 2026-10-01T13:17:49+09:00 | 3217 | UTF-8（JSON 内日文为 `\uXXXX` 转义） | 旧项目样本：JSON-stat 格式 getData 原始响应 |
| `getIndicatorInfo_category0201_rank3.json` | `https://dashboard.e-stat.go.jp/api/1.0/Json/getIndicatorInfo?Lang=JP&Category=0201&RegionalRank=3` | 200 | 2026-10-20T21:01:10+09:00 | 466496 | UTF-8 | 指标目录：人口（526 个系列） |
| `getIndicatorInfo_category0202_rank3.json` | `…/Json/getIndicatorInfo?Lang=JP&Category=0202&RegionalRank=3` | 200 | 2026-10-20T21:01:11+09:00 | 93708 | UTF-8 | 指标目录：世帯 |
| `getIndicatorInfo_category0203_rank3.json` | `…/Json/getIndicatorInfo?Lang=JP&Category=0203&RegionalRank=3` | 200 | 2026-10-20T21:01:13+09:00 | 101076 | UTF-8 | 指标目录：人口動態 |
| `getIndicatorInfo_category0204_rank3.json` | `…/Json/getIndicatorInfo?Lang=JP&Category=0204&RegionalRank=3` | 200 | 2026-10-20T21:01:14+09:00 | 32442 | UTF-8 | 指标目录：人口移動 |
| `getIndicatorInfo_category0301_rank3.json` | `…/Json/getIndicatorInfo?Lang=JP&Category=0301&RegionalRank=3` | 200 | 2026-10-20T21:01:15+09:00 | 331737 | UTF-8 | 指标目录：労働力 |
| `getIndicatorInfo_category0705_rank3.json` | `…/Json/getIndicatorInfo?Lang=JP&Category=0705&RegionalRank=3` | 200 | 2026-10-20T21:01:17+09:00 | 163793 | UTF-8 | 指标目录：国民経済計算 |
| `getData_b1_pop5x48_2021-2023CY.json` | `https://dashboard.e-stat.go.jp/api/1.0/Json/getData?Lang=JP&IndicatorCode=0201010000000010000%2C0203010000000010000%2C0203020000000010000%2C0204010000000010020%2C0204020000000010020&RegionCode=00000%2C01000%2C…%2C47000&Cycle=3&IsSeasonalAdjustment=1&TimeFrom=2021CY00&TimeTo=2023CY00&MetaGetFlg=Y&SectionHeaderFlg=1` | 200 | 2026-10-20T21:02:09+09:00 | 153856 | UTF-8 | **满批**：5 指标 × 48 地区（全国 + 47 都道府県）× 3 年 = 720 条 |
| `getData_b2_census4x48_2020CY.json` | `…/Json/getData?Lang=JP&IndicatorCode=0202000000000010000%2C0301100000010010000%2C0203060000000010010%2C0204050000000010020&RegionCode=00000%2C01000%2C…%2C47000&Cycle=3&IsSeasonalAdjustment=1&Time=2020CY00&MetaGetFlg=Y&SectionHeaderFlg=1` | 200 | 2026-10-20T21:02:10+09:00 | 38442 | UTF-8 | 4 指标 × 48 地区，单一年份；共 145 条（社会増減数 2020 年只有全国 1 条，用来测“部分缺失”） |
| `getData_b3_gdp_fy2019-2022_modifiedFrom.json` | `…/Json/getData?Lang=JP&IndicatorCode=0705100401010010000&RegionCode=00000%2C01000%2C…%2C47000&Cycle=4&IsSeasonalAdjustment=1&TimeFrom=2019FY00&TimeTo=2022FY00&modifiedFrom=20250101&MetaGetFlg=Y&SectionHeaderFlg=1` | 200 | 2026-10-20T21:02:12+09:00 | 44275 | UTF-8 | 带 `modifiedFrom` 的增量请求；年度（FY）周期；192 条 |

`RegionCode` 完整值为 `00000,01000,02000,…,47000`（48 个，URL 中逗号编码为 `%2C`），表中用 `…` 省略。

旧项目两个样本原样复制自 `digital-assets/repo/plugin-prototype/runtime/tests/fixtures/source-api/`，SHA256 与旧项目 `proof.public.json` 记录一致（metadata 对应 `retained_metadata_wrapper_sha256`，population 对应 `dashboard.population.response_sha256`）。

## 精选指标（约 10 个，码取自本目录的指标目录）

| 指标码（19 位） | 名称 | 周期 | 单位 | 调查（`@statName`） | 所在样本 |
| --- | --- | --- | --- | --- | --- |
| 0201010000000010000 | 総人口（総数） | 年（Cycle=3） | 人 | 国勢調査／人口推計 | b1 |
| 0203010000000010000 | 出生数 | 年 | 人 | 人口動態調査 | b1 |
| 0203020000000010000 | 死亡数 | 年 | 人 | 人口動態調査 | b1 |
| 0204010000000010020 | 転入者数 | 年 | 人 | 社会・人口統計体系 | b1 |
| 0204020000000010020 | 転出者数 | 年 | 人 | 社会・人口統計体系 | b1 |
| 0202000000000010000 | 世帯数（総数） | 年 | 世帯 | 国勢調査 | b2 |
| 0301100000010010000 | 就業者数（国勢調査） | 年 | 人 | 社会・人口統計体系 | b2 |
| 0203060000000010010 | 自然増減数 | 年 | 人 | 人口動態調査 | b2 |
| 0204050000000010020 | 社会増減数（総人口） | 年 | 人 | 人口推計 | b2 |
| 0705100401010010000 | 県内総生産（名目）2015年基準 | 年度（Cycle=4） | 百万円 | 県民経済計算 | b3 |

## 核对用例（从响应里直接读出）

| 文件 | 指标 / 地区 / 时间 | `$` 值 | 单位码 |
| --- | --- | --- | --- |
| b1 | 総人口 / 13000 東京都 / 2023CY00 | 14086000 | 090（人） |
| b1 | 総人口 / 00000 全国 / 2023CY00 | 124352000 | 090 |
| b1 | 出生数 / 47000 / 2022CY00 | 13594 | 090 |
| b1 | 死亡数 / 01000 / 2021CY00 | 69023 | 090 |
| b1 | 転入者数 / 27000 / 2023CY00 | 177874 | 090 |
| b1 | 転出者数 / 13000 / 2023CY00 | 385848 | 090 |
| b2 | 世帯数（総数） / 13000 / 2020CY00 | 7227180 | 094（世帯） |
| b2 | 就業者数 / 00000 / 2020CY00 | 57643225 | 090 |
| b2 | 自然増減数 / 27000 / 2020CY00 | -29766（负数） | 090 |
| b3 | 県内総生産（名目） / 13000 / 2021FY00 | 114419518 | 120（百万円） |
| b3 | 県内総生産（名目） / 00000 / 2022FY00 | 595788788 | 120 |

## 需要注意的实际行为

- `getData` 的 JSON 根节点是 `GET_STATS`，观测值在 `STATISTICAL_DATA.DATA_INF.DATA_OBJ[].VALUE`，数值是字符串 `$`；全国行的 `@regionRank` 为 `2`，都道府県为 `3`（同一批里混在一起）。
- **请求参数 `modifiedFrom`，响应 `PARAMETER` 里回显为 `ModifiedFrom`（首字母大写）**；其他参数回显为小写开头（`indicatorCode`、`timeFrom`…）。解析时不要把回显的键名拿去拼请求。
- b3 的 `modifiedFrom=20250101` 仍返回了 FY2019–2022 全部 192 条：说明这些记录的更新日期都在 2025-01-01 之后，或者过滤粒度与预期不同。增量逻辑不能假设“带 modifiedFrom 结果一定更少”。
- 本次 3 个批次 `@isProvisional` 全为 `0`（確報）。
- 指标目录按 `Category` + `RegionalRank=3` 缩小范围，并非全量目录（全量约 6000 系列）。
