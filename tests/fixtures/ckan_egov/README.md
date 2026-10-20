# ckan_egov：e-Gov データポータル（CKAN）离线样本

供 DATUMOJ-IVY-401（CKAN 采集器）、DATUMOJ-IVY-402（目录解析与检索）、DATUMOJ-IVY-703（目录评测题）离线测试使用。

- 录制时间：2026-10-20 21:02–21:07（JST，UTC+9），User-Agent `DATUMOJ-fixture-recorder/0.1 (+https://github.com/gorou5/datumoj)`，每秒不超过 1 个请求，不跟随重定向。
- API 基址：`https://data.e-gov.go.jp/data/api/action/{action}`（来自 e-Gov 官方 API 指南）。`https://data.e-gov.go.jp/robots.txt` 返回 404（没有 robots 限制）。
- **字节说明**：文件内容就是 HTTP 响应体，只去掉了传输层的 `Content-Encoding: gzip`（API 响应全部是 gzip 传输），没有格式化、没有重新编码。每个文件的 URL、状态码、响应头（去掉了 `Set-Cookie`）、抓取时间、SHA-256 都在 `_responses.json` 里。
- 完整性校验：`SHA256SUMS`（覆盖除 README.md 外的全部文件；在本目录运行 `shasum -a 256 -c SHA256SUMS`）。

## 许可与署名

- **目录元数据（JSON）**：e-Gov データポータル的目录 API 响应。站点利用规约见 <https://data.e-gov.go.jp/info/terms>（未逐条做法律判断）。出处：「e-Govデータポータル（https://data.e-gov.go.jp/）」。
- **资源文件**（只下载了资源级许可明确为 CC BY、托管在 go.jp 上的文件）：
  - `resources/syukujitsu.csv`：出典「内閣府『国民の祝日』について」（https://www8.cao.go.jp/chosei/shukujitsu/gaiyou.html）。目录中资源许可 `cc-by`；内閣府网站按政府標準利用規約（第2.0版）发布，与 CC BY 4.0 兼容。
  - `resources/npa_keibigyo_reiwa7data.csv`：出典「警察庁『警備業データ』」（https://www.npa.go.jp/publications/statistics/safetylife/reiwa7data.csv）。目录中资源许可 `CC-BY`（大写，原样保留）。
- 目录里托管在 `data.bodik.jp` 的资源（福岡市、静岡市的数据集）不在允许访问的 go.jp / lg.jp 范围内，没有下载；公正取引委員会的资源许可为空字符串，也没有下载。
- 元数据中没有个人信息：联系方式字段只有部门名或空字符串；`creator_user_id` / `user_id` 是发布账号的 UUID，原样保留。

## 文件一览

| 文件名 | 来源 URL | HTTP 状态 | 抓取时间（JST） | 大小（字节） | 编码 | 用途（测试点） |
|---|---|---|---|---|---|---|
| `package_search_q-jinko_rows5_start0.json` | `…/package_search?q=人口&rows=5&start=0&sort=metadata_modified desc` | 200 | 2026-10-20T21:02:05+09:00 | 80343 | UTF-8 JSON（中文日文是 `\uXXXX` 转义） | 401 分页第 1 页；`count=1733`；402 解析 `results[]`、`search_facets` |
| `package_search_q-jinko_rows5_start5.json` | 同上，`start=5` | 200 | 2026-10-20T21:02:22+09:00 | 23902 | 同上 | 401 分页第 2 页（start 是记录偏移，不是页码） |
| `package_search_q-jinko_rows5_start100000_empty.json` | 同上，`start=100000` | 200 | 2026-10-20T21:02:24+09:00 | 232 | 同上 | 401 超出总数：`success=true`、`count=1733`、`results=[]`，必须据此停止翻页 |
| `package_search_rows0_facets_license_format.json` | `…/package_search?q=*:*&rows=0&facet.limit=50&facet.mincount=1&facet.field=["license_id","res_license_id","res_format"]` | 200 | 2026-10-20T21:06:48+09:00 | 4773 | 同上 | 计数模式（rows=0，`results=[]` 但 `count=18826`）；许可值、格式值的规范化（见下文“观察”） |
| `recently_changed_packages_activity_list_limit5.json` | `…/recently_changed_packages_activity_list?limit=5` | 200 | 2026-10-20T21:02:29+09:00 | 1941 | 同上 | 401 增量同步：活动 `id`、`timestamp`（UTC、无时区后缀）、`object_id`、`activity_type="changed package"`；同一数据集连续 3 次出现，用来测去重 |
| `package_show_cao_syukujitsu.json` | `…/package_show?id=95eac548-e03d-4800-9d02-82e5baa06b2b` | 200 | 2026-10-20T21:02:43+09:00 | 4395 | 同上 | 402：数据集许可 `null`、资源许可 `cc-by`；`tags=[]`；有 `extras[]`；`frequency_of_update="1nen "`（带尾随空格）；资源 `size=19000` 但实际文件 21538 字节，`mimetype=null` |
| `package_show_npa_keibigyo.json` | `…/package_show?id=1ed8e7e3-f412-4b5a-8d29-3941a5b6a2d5` | 200 | 2026-10-20T21:03:49+09:00 | 3929 | 同上 | 402：资源许可大写 `CC-BY`；数据集许可 `null`；`tags=[]` |
| `package_show_fukuoka_census_h22_1-6.json` | `…/package_show?id=275043f1-d8a4-418f-96df-1aba07f1eaa2` | 200 | 2026-10-20T21:02:44+09:00 | 59313 | 同上 | 402：30 个 XLSX 资源；组织、标签、分组齐全；数据集许可 `null` vs 资源许可 `cc-by`（两级不一致）；资源 URL 在 `data.bodik.jp`（外部主机） |
| `package_show_jftc_nenpou_r7.json` | `…/package_show?id=f815c763-f968-4ff5-94ae-03d502d7167a` | 200 | 2026-10-20T21:03:51+09:00 | 34487 | 同上 | 402：25 个资源（PDF×1、XLSX×24），**两级许可都缺失**（数据集 `null`、资源 `""`），`tags=[]`、`groups=[]` |
| `resources/syukujitsu.csv` | `https://www8.cao.go.jp/chosei/shukujitsu/syukujitsu.csv` | 200 | 2026-10-20T21:03:58+09:00 | 21538 | **cp932**（无 BOM）；响应头 `Content-Type: text/csv`，**没有 charset** | 301/402：cp932 CSV 嗅探；`Last-Modified: Mon, 02 Feb 2026 00:30:17 GMT` |
| `resources/npa_keibigyo_reiwa7data.csv` | `https://www.npa.go.jp/publications/statistics/safetylife/reiwa7data.csv` | 200 | 2026-10-20T21:03:59+09:00 | 8429 | **UTF-8 带 BOM**；响应头 `text/csv`，无 charset | 301：多段表格塞在一个 CSV 里、引号内换行的表头、`"589,938"` 千位分隔、`9.1%` 百分比、全角空格缩进 |

（来源 URL 中 `…` = `https://data.e-gov.go.jp/data/api/action`；完整、已编码的 URL 见 `_responses.json`。）

请求次数：本次录制共发出 29 个请求（全部 ≤1 次/秒）。其中进入本目录 11 个；robots.txt 6 个（data.e-gov.go.jp、www8.cao.go.jp、www.npa.go.jp、www.e-stat.go.jp、www.city.fukuoka.lg.jp、www.nta.go.jp）；探索性检索 6 个和 1 个未收录的 package_show（静岡市観光交流客数，与已收录样本重复）；其余 5 个用于 `tabular/`（2 个 e-Stat XLSX 被保存，3 个失败或不合用，见 `../tabular/README.md`）。

## 数据集一览

供 DATUMOJ-IVY-703 出目录评测题（“有没有关于 X 的数据”）。许可两列都是 API 原值：数据集级取 `result.license_id`，资源级是 `resources[].license_id` 的去重计数。“×n”表示资源个数。

| 数据集 id | 标题 | 组织（name） | 数据集许可 | 资源许可 | 资源格式 | 标签 | 所在样本 |
|---|---|---|---|---|---|---|---|
| `275043f1-d8a4-418f-96df-1aba07f1eaa2` | 平成２２年　国勢調査　１．人口等基本集計結果　１－６　住居の状態 | 福岡市（`org_401307`） | `null` | `cc-by`×30 | XLSX×30 | 統計 | search p1、`package_show_fukuoka_census_h22_1-6.json` |
| `2f7e85f5-60c2-4bf1-a4b2-7a2d633b819a` | 静岡市観光交流客数 | 静岡市（`org_221007`） | `null` | `cc-by`×1 | CSV×1 | 統計、観光 | search p1 |
| `af6f1c32-1a05-418a-b8b7-68d62ed272d4` | 福岡市　平成２２年　国勢調査　５．従業地・通学地に関する人口・産業等集計結果 概要 | 福岡市（`org_401307`） | `null` | `cc-by`×1 | PDF×1 | 統計 | search p1 |
| `d5c6ca2a-97a0-484c-b685-9c3a49e47dbb` | 福岡市　死亡者数（日常生活圏域別） | 福岡市（`org_401307`） | `null` | `cc-by`×1 | CSV×1 | 健康、福祉、統計 | search p1 |
| `fc325ba2-0966-4a73-ac8b-9b635805a922` | 平成２２年　国勢調査　１．人口等基本集計結果 | 福岡市（`org_401307`） | `null` | `cc-by`×1 | PDF×1 | 統計 | search p1 |
| `fe24a2c3-cabb-4247-8759-26a3a902d817` | 静岡市の人口・世帯 | 静岡市（`org_221007`） | `null` | `other-open`×1 | HTML×1 | （无） | search p2 |
| `097cf0b2-044a-420d-86bd-f98ac23c607f` | 福岡市　転入者数（日常生活圏域別） | 福岡市（`org_401307`） | `null` | `cc-by`×1 | CSV×1 | 人口、統計 | search p2 |
| `8948b0a9-e597-4bfe-9413-9fc841c64e5e` | 福岡市　平成２７年　国勢調査　１．人口等基本集計結果 | 福岡市（`org_401307`） | `null` | `cc-by`×1 | PDF×1 | 統計 | search p2 |
| `11e4f530-5e67-4e7d-ba09-e8ef913c2285` | 平成２２年　国勢調査　１．人口等基本集計結果　１－７　高齢世帯員の有無による世帯の類型 | 福岡市（`org_401307`） | `null` | `cc-by`×1 | XLSX×1 | 統計 | search p2 |
| `8eb7d1ce-7d8b-48c7-a072-dd0d6800dd42` | 福岡市　平成２２年　国勢調査　11．追加集計結果・その他　大都市別の主要統計表 | 福岡市（`org_401307`） | `null` | `cc-by`×1 | XLSX×1 | 統計 | search p2 |
| `95eac548-e03d-4800-9d02-82e5baa06b2b` | 「国民の祝日」について（毎年更新） | 内閣府（`org_0400`） | `null` | `cc-by`×1 | CSV×1 | （无） | `package_show_cao_syukujitsu.json` |
| `1ed8e7e3-f412-4b5a-8d29-3941a5b6a2d5` | 警備業データ | 警察庁（`org_0700`） | `null` | `CC-BY`×1 | CSV×1 | （无） | `package_show_npa_keibigyo.json` |
| `f815c763-f968-4ff5-94ae-03d502d7167a` | 令和７年度_公正取引委員会年次報告 | 公正取引委員会（`org_0600`） | `null` | `""`×25 | PDF×1、XLSX×24 | （无） | `package_show_jftc_nenpou_r7.json` |

出题提示：上表 13 个数据集覆盖人口/国勢調査（福岡市 6 个）、観光（静岡市）、死亡者数・転入者数、祝日、警備業、独占禁止法の年次報告。可以出“有答案”的题（如“福岡市的转入人口”“国民の祝日一覧”“警備員数”），也可以出“本样本里没有”的题（如“东京的地价”“气象”），检验空结果不被说成“政府没有这类数据”。

## 和文档不一致 / 值得注意的观察

1. **数据集级许可在全站都是空的**：`package_search_rows0_facets_license_format.json` 中 18826 个数据集的 `license_id` 分面为空；抽到的 `package_show` 全是 `license_id=null`、`license_title=null`。许可只能从资源级读取。指南第 7 节“资源许可 + 可选数据集许可”的说法成立，但“数据集许可”实际上一个都没有。
2. **资源级许可值不统一**（同一分面里原样出现）：`cc-by`（15348）、`CC-BY`（679）、`gjstu`（2342）、`政府標準利用規約（第 2.0 版）`（64）、`cc-by-4.0`（27）、`other-open`（19）、`pdl-1.0`（7）、`cc-by-2.1-jp`、`cc-by-nc`、`cc-by-nc-sa`（各 1）、单个空格 `" "`（149）；`package_show_jftc_nenpou_r7.json` 里还有空字符串 `""`。
3. **资源格式值不统一**：`res_format` 分面有全角 `ＣＳＶ`、`ＸＬＳＸ`，带点的 `.CSV`、`.XLSX`、`.ZIP`，小写 `php`、`md`，以及 `" "`、`"0"`、`その他`、`ビジュアル分析(Tableau)`。
4. **资源 URL 常在外部主机**：福岡市、静岡市的资源都在 `data.bodik.jp`。采集器只记录 URL，不应假定在 go.jp 下。
5. **链接失效很常见**（本次只当观察，没有保存响应体）：福岡市漁業センサス资源 `https://www.city.fukuoka.lg.jp/data/open/cnt/3/62138/1/25_02.xlsx` 返回 404；国税庁 `https://www.nta.go.jp/publication/statistics/kokuzeicho/minkan2020/xls/04.xlsx` 返回 302 跳到 `/error/404.htm`。
6. 资源 `size` 是发布方填写的约数，不可当实际大小：祝日 CSV 登记 19000，实际 21538 字节。
7. 时间戳无时区后缀（如 `"2026-10-19T13:04:22.729549"`），按官方说明是 UTC。
8. 指南说 `package_search` 默认 `facet=true`；本次分页请求未传 `facet`，响应里确有 `facets`/`search_facets`（为空对象，因为没指定 `facet.field`）。
9. `recently_changed_packages_activity_list` 可用，返回 `result[]` 列表（不是 `result.results[]`），没有总数字段；`limit=5` 正常生效。
