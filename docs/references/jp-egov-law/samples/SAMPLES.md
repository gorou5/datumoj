# 样本说明（jp-egov-law）

- 本目录的文件是 37 在 2026-10-19 对活动服务发出的两次 GET 请求的**原始响应**，按字节保存，没有重新编码、没有格式化、没有改动任何字符。校验值见同目录的 `SHA256SUMS`。
- 这两个请求都没有带任何认证信息：本 API 没有 Key，请求 URL 里也没有要去掉的凭据参数。
- 本目录不含 `.meta.json`、不含自造样本、不含合成数据；元信息以本文件的文字给出。
- 许可与再分发的依据见上一级目录的 `terms.md` 第 8 节；署名写法见下。

## 1. `egov_law_data_flag_real.json`

| 项目 | 内容 |
|---|---|
| 来源 URL | `https://laws.e-gov.go.jp/api/2/law_data/411AC0000000127?response_format=json` |
| 抓取时间 | 2026-10-19T11:07:00+09:00（JST） |
| HTTP 状态 / Content-Type | 200 / `application/json` |
| 字节数 / sha256 | 10,797 / `4bc87f755eb44e783ea8443f7c86497c3afcba58eeecec2c46c189ba3b356a62` |
| 用途 | `law_data` 成功响应的真实样本：用来核对顶层字段（`attached_files_info`、`law_info`、`revision_info`、`law_full_text`）与正文树的结构（`tag`／`attr`／`children`、`Article` 的 `Delete`／`Hide`／`Num`、`Paragraph`／`Sentence`） |

**字段显示什么（中性描述）**

- 顶层 4 个键：`attached_files_info`、`law_info`、`revision_info`、`law_full_text`。
- `law_info` 8 个字段：`law_type` 为 `Act`，`law_id` 为 `411AC0000000127`，`law_num` 为 `平成十一年法律第百二十七号`，`law_num_era` 为 `Heisei`，`law_num_year` 是数字 11，`law_num_type` 为 `Act`，`law_num_num` 为 `127`，`promulgation_date` 为 `1999-08-13`。
- `revision_info` 21 个字段：`law_revision_id` 为 `411AC0000000127_19990813_000000000000000`，`updated` 为 `2024-07-21T13:56:59+09:00`，`amendment_promulgate_date` 与 `amendment_enforcement_date` 都是 `1999-08-13`，`amendment_type` 为 `1`，`mission` 为 `New`，`repeal_status` 为 `None`，`current_revision_status` 为 `CurrentEnforced`；可空字段（`amendment_law_id`、`amendment_law_title`、`repeal_date` 等）是 `null`。
- `attached_files_info` 是对象，`image_data` 为空字符串，`attached_files` 有 2 个条目，`src` 形如 `./pict/H11HO127-001.jpg`。
- `law_full_text` 是对象：根 `tag` 为 `Law`，`attr` 有 `Era`／`Lang`／`LawType`／`Num`／`Year`／`PromulgateMonth`／`PromulgateDay`；`LawBody` 下有 `LawTitle`、`MainProvision`（2 个 `Article`）、`SupplProvision`（3 个 `Paragraph`）与 2 个 `AppdxNote`；`children` 里既有对象也有字符串。

## 2. `egov_laws_search_minpaku_title_real.json`

| 项目 | 内容 |
|---|---|
| 来源 URL | `https://laws.e-gov.go.jp/api/2/laws?law_title=%E4%BD%8F%E5%AE%85%E5%AE%BF%E6%B3%8A%E4%BA%8B%E6%A5%AD%E6%B3%95&limit=3`（解码后为 `law_title=住宅宿泊事業法&limit=3`） |
| 抓取时间 | 2026-10-19T11:07:01+09:00（JST） |
| HTTP 状态 / Content-Type | 200 / `application/json` |
| 字节数 / sha256 | 6,414 / `e466dbfe14f629bd95ebf719e811d75e9f94be23809f537b5c922c01788e2889` |
| 用途 | `laws` 列表响应与分页字段的真实样本：用来核对 `total_count`／`count`／`next_offset` 的含义，以及 `laws[]` 下 `law_info`／`revision_info`／`current_revision_info` 三层结构 |

**字段显示什么（中性描述）**

- 顶层 3 个键：`total_count` 为 6、`count` 为 3、`next_offset` 为 3；`laws` 是长度为 3 的数组。
- 每个 `laws[]` 有 `law_info`、`revision_info`、`current_revision_info` 三个对象；`revision_info` 与 `current_revision_info` 结构相同。
- 第一条的 `law_info.law_id` 是 `429AC0000000065`，`law_num` 是 `平成二十九年法律第六十五号`，`revision_info.law_title` 是 `住宅宿泊事業法`，`abbrev` 是 `民泊法,民泊新法`，`law_revision_id` 是 `429AC0000000065_20250601_504AC0000000068`。
- 数组里还有 `law_type` 为 `CabinetOrder` 与 `MinisterialOrdinance` 的条目（施行令与施行规则），说明 `law_title` 的检索会命中同名的下位法令；使用时要按 `law_type` 与法令名自己核对，不能直接取第一条。

## 3. 署名写法（依许可证据）

- e-Gov 利用规约给出的记载例：`出典：e-Govポータル （https://www.e-gov.go.jp）`（原文：https://www.e-gov.go.jp/terms ；核对：2026-10-19）。
- PDL1.0 的记载例还包含“当该页面的 URL”与“（○年○月○日に利用）”两项；公开这两个样本时，建议连同本文件上表的来源 URL 与抓取时间一起给出，读者就能复现同一次请求。
- 如果对样本做加工（例如转成 CSV、截取条文），按 e-Gov 利用规约的要求，要在出典之外另写“做过加工以及加工的主体”。
- 依据与引文见 `../terms.md` 第 3 节与第 8 节。

## 4. 字节未改动的声明

两个文件都是上述两次请求响应的逐字节副本：没有转码、没有重新排版、没有增删空白或换行、没有改扩展名以外的任何处理。复制由脚本完成，并在复制后重新计算 sha256，与来源文件的记录值逐一比对，结果一致（见 `SHA256SUMS`）。
