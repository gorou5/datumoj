# jp-egov-law 采集笔记（e-Gov 法令API Version 2）

- 本笔记按以下验证标签区分证据范围。
- 本文件的每一条陈述后面都带一个验证程度标签，四个标签只用这四个：
  - `真实请求验证过`：保存的真实请求响应显示相应结果（具体响应由证据标签标明）。
  - `读过官方页面（2026-10-19）` / `读过官方页面（2026-10-20）`：当日直接读取官方页面或定义文件得到。
  - `仅读文档`：仅由保存的规格文本、代码表或说明材料支持，未以真实响应验证。
  - `推测`：没有直接证据，由已知事实推出来的判断，文中会写明依据。
- 证据标签（本文件只使用这几个）：`S1 terms`、`S2 spec`、`S3 plan`、`S4 samples`、`S5 urls`、`S6 page`、`S7 addendum`、`S8 robots`、`S9 page`、`S10 source summary`。
- 上述标签指向 37 内部的文档与保存的页面，均不属于本资料包。
- `S4 samples` 指随包提交的两个真实响应样本，其来源 URL、抓取时间与用途写在 `samples/SAMPLES.md`。
- 本文件没有、也不会有个人信息、密钥、帐号或本机路径。
- 说明：下面关于本文件自身构成的叙述（例如“本资料收录两个样本”）不是对本 API 的陈述，因此不加标签。

---

## 1. 端点与入口

### 1.1 基础信息

- 基础 URL 是 `https://laws.e-gov.go.jp/api/2`（定义文件的 `servers` 段）。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- 定义文件：`https://laws.e-gov.go.jp/api/2/swagger-ui/lawapi-v2.yaml`，OpenAPI 3.0.3，`info.title` 为 `法令API Version 2`，`info.version` 为 `2.1.139`，125,750 字节。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- 同一份定义文件在 2026-10-19 也读过一次，两次的 sha256 相同（`342e2760ffcafc6b43f966e4a2b1e31dc148ea85e7f0290306237d48eabc6fdc`），即这两天之间定义没有变化。[真实请求验证过] 〔证据:S6 page〕〔证据:S9 page〕
- 两次读取的时间分别是 2026-10-19 11:04:08 与 2026-10-20 12:07:00（JST）。[真实请求验证过] 〔证据:S5 urls〕〔证据:S9 page〕
- 定义文件以 `application/x-www-form-urlencoded` 作为 Content-Type 返回（两次都是），不是 `text/yaml`；按文本读即可，不要用 Content-Type 判断内容。[真实请求验证过] 〔证据:S6 page〕〔证据:S9 page〕
- 所有 operation 都只有 `GET`，没有 POST/PUT。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- 定义文件里没有 `securitySchemes`，任何 operation 也没有 `security` 要求；2026-10-19 与 2026-10-20 的真实请求都没有带认证头，全部返回 HTTP 200。[读过官方页面（2026-10-20）]（无认证要求）／[真实请求验证过]（无认证可取值） 〔证据:S9 page〕〔证据:S4 samples〕
- 因此不需要 Key，也没有申请流程可写。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- `https://laws.e-gov.go.jp/api/2/` 本身返回 HTTP 200 且正文 0 字节——它不是落地页，不能拿它判断服务是否正常。[真实请求验证过] 〔证据:S6 page〕
- 站点首页 `/`、说明页 `/apitop/`、帮助页 `/help/` 都返回同一个 800 字节的前端外壳（HTML 中含有 JavaScript 脚本资源，正文需由 JavaScript 渲染），取不到任何说明文字；因此本资料关于 API 的规格全部以定义文件为准，不以这三个页面为准。[真实请求验证过] 〔证据:S6 page〕
- 定义文件另提到两个没有取过的入口：`https://laws.e-gov.go.jp/docs/`（法令ドキュメンテーション）与 `https://laws.e-gov.go.jp/api/2/redoc/`（Redoc 版说明）。[读过官方页面（2026-10-20）] 〔证据:S9 page〕

### 1.2 6 个端点

| # | 路径 | 定义里的名称 | operationId | 用途 | 位置/查询的必填项 |
|---|---|---|---|---|---|
| E1 | `/laws` | 法令一覧取得API | `get-laws` | 按条件列法令，取 `law_id`、`law_num` 与各版元信息 | 无必填 |
| E2 | `/law_revisions/{law_id_or_num}` | 法令履歴一覧取得API | `get-revisions` | 一部法令的全部改正历史 | 路径：法令 ID 或法令番号 |
| E3 | `/law_data/{law_id_or_num_or_revision_id}` | 法令本文取得API | `get-law_data` | 法令正文（本资料最主要的端点） | 路径：ID／番号／履历 ID |
| E4 | `/keyword` | キーワード検索API | `get-keyword` | 在法令正文内全文检索 | 查询：`keyword` |
| E5 | `/attachment/{law_revision_id}` | 添付ファイル取得API | `get-attachment` | 正文里的附图／PDF 附件 | 路径：履历 ID |
| E6 | `/law_file/{file_type}/{law_id_or_num_or_revision_id}` | 法令本文ファイル取得API | `get-law_file` | 整部法令的下载文件（xml/json/html/rtf/docx） | 路径：文件种别＋ID |

上表 6 行的路径、名称、operationId 全部逐字取自定义文件的 `paths` 段。[读过官方页面（2026-10-20）] 〔证据:S9 page〕

### 1.3 批量下载（定义文件里写明的 URL 形式，不是上表的 6 个端点）

定义文件有一个小节，标题是「XML一括ダウンロードについて」，给出三种 URL 形式：[读过官方页面（2026-10-20）] 〔证据:S9 page〕

| 用途 | URL 形式（按定义原文） |
|---|---|
| 全部法令数据 | `https://laws.e-gov.go.jp/bulkdownload?file_section=1&only_xml_flag=true` |
| 按法令分类 | `https://laws.e-gov.go.jp/bulkdownload?file_section=2&category_cd={カテゴリーコード}&only_xml_flag=true` |
| 最新的更新法令数据 | `https://laws.e-gov.go.jp/bulkdownload?file_section=3&update_date={取得する法令データの日付}&only_xml_flag=true` |

- 上表三行的 URL 与参数名逐字取自定义文件；`{カテゴリーコード}` 与 `{取得する法令データの日付}` 是原文里的占位符。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- 日期用 `YYYYMMDD`（原文举例：2024 年 1 月 1 日写作 `20240101`）。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- 增量窗口只有三个月：「更新法令データの取得範囲は過去３ヶ月です。」（原文：定义文件同节；核对：2026-10-20）[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- 按分类下载时，`category_cd` 的第 2、3 位的 0 要去掉，原文举例：宪法用 `1` 而不是 `001`。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- 这三个 URL 在证据里只是被抄录，没有实际下载过；因此压缩包的内容、编码、目录结构都未知。[仅读文档] 〔证据:S7 addendum〕
- 定义文件在同一节里还写「以下のURLを利用してダウンロードしてください。」，即把批量下载写成整批取数的官方路径；但同一节没有写它是否需要申请、是否限速、是否与 JSON API 共用配额。[读过官方页面（2026-10-20）]（引文）／[仅读文档]（缺口） 〔证据:S9 page〕〔证据:S7 addendum〕

---

## 2. 参数

下面 6 张表的参数名、位置、类型、必填、默认值全部由 2026-10-20 保存的定义文件逐条抽出，并与 2026-10-19 读取的内容一致。除特别注明外，每行的标签都是 `读过官方页面（2026-10-20）`，证据标签 `S9 page`。

### 2.1 E1 `/laws`

| 参数 | 类型 | 必填 | 允许值 / 默认 | 要点 |
|---|---|---|---|---|
| `law_id` | string | 否 | — | 法令 ID，部分一致 |
| `law_num` | string | 否 | — | 法令番号，部分一致（例：`昭和二十二年政令第十六号`） |
| `law_num_era` | enum | 否 | `Meiji` `Taisho` `Showa` `Heisei` `Reiwa` | 法令番号的年号 |
| `law_num_num` | string | 否 | — | 号数（例：`88`） |
| `law_num_type` | enum | 否 | 与 `law_type` 同为 7 值 | 法令番号的法令种别 |
| `law_num_year` | integer | 否 | — | 年号内的年份（例：`60`） |
| `law_title` | string | 否 | — | 法令名或法令略称，部分一致（略称也会命中） |
| `law_title_kana` | string | 否 | — | 法令名读音，部分一致 |
| `law_type` | array（csv，`explode: false`） | 否 | `Constitution` `Act` `CabinetOrder` `ImperialOrder` `MinisterialOrdinance` `Rule` `Misc` | 可多值，逗号连接（例：`Act,Rule`） |
| `amendment_law_id` | string | 否 | — | 改正法令的 ID，部分一致；**指定它时 `asof` 被忽略**（定义原文注明） |
| `asof` | string（date） | 否 | `YYYY-MM-DD` | 法令的时点：把该时点以前最近的改正历史放进各法令的 `revision_info`；省略时按现在检索 |
| `category_cd` | array（csv） | 否 | `001`…`050`（50 个事项别分类码） | 事项别分类码，可多值 |
| `mission` | array（csv） | 否 | `New` `Partial` | 新规制定／一部改正 |
| `omit_current_revision_info` | boolean | 否 | 默认 `false` | `true` 时不返回 `current_revision_info` |
| `promulgation_date_from` | string（date） | 否 | `YYYY-MM-DD` | 公布日下限（含当日） |
| `promulgation_date_to` | string（date） | 否 | `YYYY-MM-DD` | 公布日上限（含当日） |
| `repeal_status` | array（csv） | 否 | `None` `Repeal` `Expire` `Suspend` `LossOfEffectiveness` | 定义原文提醒：`Expire`／`LossOfEffectiveness` 时 `repeal_date` 是数据废止处理日，不是法律上的废止日 |
| `limit` | integer（int32） | 否 | 默认 `100`；**没有写最大值** | `laws` 的取得件数上限 |
| `offset` | integer（int32） | 否 | 默认 `0` | 取得开始位置，0 起算（原文：给 `10` 就从第 11 件开始） |
| `order` | string | 否 | 默认 `law_info.law_id` | 可多字段；`+` 升序、`-` 降序，无符号按升序；只能指 `law_info`／`revision_info` 下的字段 |
| `response_format` | enum | 否 | `json` `xml`；默认由 `Accept` 判断，判断不了用 `json` | 响应形式 |

### 2.2 E2 `/law_revisions/{law_id_or_num}`

| 参数 | 类型 | 必填 | 允许值 / 默认 | 要点 |
|---|---|---|---|---|
| `law_id_or_num` | string | **是（路径）** | — | 法令 ID 或法令番号，**完全一致** |
| `law_title` | string | 否 | — | 法令名或略称；不用 `/` 包起来是部分一致，用 `/` 包起来按正则**完全一致**（原文例：`/^デジタル庁設置法$/`） |
| `law_title_kana` | string | 否 | — | 法令名读音，部分一致 |
| `amendment_date_from` | string（date） | 否 | `YYYY-MM-DD` | 改正法令施行期日下限（含） |
| `amendment_date_to` | string（date） | 否 | `YYYY-MM-DD` | 改正法令施行期日上限（含） |
| `amendment_law_id` | string | 否 | — | 改正法令 ID，部分一致 |
| `amendment_law_num` | string | 否 | — | 改正法令番号，部分一致 |
| `amendment_law_title` | string | 否 | — | 改正法令名；部分一致或 `/正则/` 完全一致 |
| `amendment_law_title_kana` | string | 否 | — | 改正法令名读音，部分一致 |
| `amendment_promulgate_date_from` | string（date） | 否 | `YYYY-MM-DD` | 改正法令公布日下限（含） |
| `amendment_promulgate_date_to` | string（date） | 否 | `YYYY-MM-DD` | 改正法令公布日上限（含） |
| `amendment_type` | array（csv） | 否 | `1` `3` `8` | 改正种别，可多值 |
| `category_cd` | array（csv） | 否 | `001`…`050` | 事项别分类码 |
| `current_revision_status` | array（csv） | 否 | `CurrentEnforced` `UnEnforced` `PreviousEnforced` `Repeal` | 履历状态；定义原文注明：**与 `asof` 的取值无关，始终与现在的状态比较** |
| `mission` | array（csv） | 否 | `New` `Partial` | 新规制定／一部改正 |
| `remain_in_force` | boolean | 否 | — | 废止后是否仍有効力（`true` 有／`false` 无） |
| `repeal_date_from` | string（date） | 否 | `YYYY-MM-DD` | 废止日下限 |
| `repeal_date_to` | string（date） | 否 | `YYYY-MM-DD` | 废止日上限 |
| `repeal_status` | array（csv） | 否 | `None` `Repeal` `Expire` `Suspend` `LossOfEffectiveness` | 废止等状态 |
| `updated_from` | string（date） | 否 | `YYYY-MM-DD` | 数据更新日下限 |
| `updated_to` | string（date） | 否 | `YYYY-MM-DD` | 数据更新日上限 |
| `response_format` | enum | 否 | `json` `xml` | 响应形式 |

本端点没有 `limit`／`offset`，一次返回该法令的全部履历，顺序是 `law_revision_id` 由新到旧（定义原文如此）。[读过官方页面（2026-10-20）] 〔证据:S9 page〕

### 2.3 E3 `/law_data/{law_id_or_num_or_revision_id}`

| 参数 | 类型 | 必填 | 允许值 / 默认 | 要点 |
|---|---|---|---|---|
| `law_id_or_num_or_revision_id` | string | **是（路径）** | — | 法令 ID、法令番号或履历 ID，**完全一致**（三种形式都收） |
| `law_full_text_format` | enum | 否 | `json` `xml`；默认跟随 `response_format` | 只决定 `law_full_text` 的格式 |
| `asof` | string（date） | 否 | `YYYY-MM-DD` | 取该时点以前最近一次履历对应的正文；**路径里给的是履历 ID 时被忽略** |
| `elm` | string | 否 | 元素选择器 | 只取部分要素；语法在定义里有两处不一致（见 8.5），建议不用 |
| `json_format` | enum | 否 | `full`（默认）／`light` | 只对 JSON 有效；`full` 是详细树，`light` 是便于解析的简化结构 |
| `omit_amendment_suppl_provision` | boolean | 否 | 默认 `false` | `true` 时正文里不含改正法令的附则 |
| `include_attached_file_content` | boolean | 否 | 默认 `false` | `true` 时把附件内容放进 `attached_files_info.image_data` |
| `response_format` | enum | 否 | `json` `xml`；默认由 `Accept` 判断，判断不了用 `json` | 响应形式；与 `law_full_text_format` 不一致时正文会被 Base64 编码（见 8.4） |

### 2.4 E4 `/keyword`

| 参数 | 类型 | 必填 | 允许值 / 默认 | 要点 |
|---|---|---|---|---|
| `keyword` | string | **是** | — | 检索词；支持通配符 `*`（0 字以上）、`?`（1 字），以及 AND／OR／NOT 检索式；定义原文注明通配符与布尔式**不能组合** |
| `law_num` | string | 否 | — | 法令番号，部分一致 |
| `law_num_era` | enum | 否 | 5 个年号 | 法令番号的年号 |
| `law_num_num` | string | 否 | — | 号数 |
| `law_num_type` | enum | 否 | 7 值 | 法令番号的法令种别 |
| `law_num_year` | integer | 否 | — | 年号内的年份 |
| `law_type` | array（csv） | 否 | 7 值 | 法令种别 |
| `asof` | string（date） | 否 | `YYYY-MM-DD` | 法令的时点 |
| `category_cd` | array（csv） | 否 | `001`…`050` | 事项别分类码 |
| `promulgation_date_from` | string（date） | 否 | `YYYY-MM-DD` | 公布日下限 |
| `promulgation_date_to` | string（date） | 否 | `YYYY-MM-DD` | 公布日上限 |
| `limit` | integer（int32） | 否 | 默认 `100`；**上限 `1000`** | 定义原文：这是 `sentences` 的 `position` 数**总和**的上限，不是法令件数 |
| `offset` | integer（int32） | 否 | 默认 `0` | 取得开始位置 |
| `order` | string | 否 | 默认 `law_info.law_id` | 排序，规则同 E1 |
| `response_format` | enum | 否 | `json` `xml` | 响应形式 |
| `sentences_limit` | integer（int32） | 否 | — | 限制每个法令显示的 `sentences` 件数；定义原文提醒指定后可能与 `limit` 的件数不一致，且超过 `limit` 时以 `limit` 为准 |
| `sentence_text_size` | integer（int32） | 否 | 默认 `100` | `sentences[].text` 的显示字数，**含 `highlight_tag` 指定的标签** |
| `highlight_tag` | string | 否 | 默认 `span` | 命中处外包的 HTML 标签名（例：`em`） |

### 2.5 E5 `/attachment/{law_revision_id}`

| 参数 | 类型 | 必填 | 允许值 / 默认 | 要点 |
|---|---|---|---|---|
| `law_revision_id` | string | **是（路径）** | — | 履历 ID，完全一致 |
| `src` | string | 否 | — | 取 `attached_files_info.attached_files[].src` 的值（就是法令 XML 里 `Fig` 元素的 `src` 属性）；不给 `src` 时返回全部附件的 Zip |

### 2.6 E6 `/law_file/{file_type}/{law_id_or_num_or_revision_id}`

| 参数 | 类型 | 必填 | 允许值 / 默认 | 要点 |
|---|---|---|---|---|
| `law_id_or_num_or_revision_id` | string | **是（路径）** | — | 三种 ID 形式，完全一致 |
| `file_type` | enum | **是（路径）** | `xml` `json` `html` `rtf` `docx` | 文件种别 |
| `asof` | string（date） | 否 | `YYYY-MM-DD` | 取该时点以前最近履历的文件；路径里是履历 ID 时被忽略 |

### 2.7 参数层面的两点补充

- E1 的定义原文写明“没有必填参数，也不必组合全部参数”。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- 除 `keyword`、各个路径参数与 `file_type` 之外，所有查询参数都是可选。定义里只有 `law_type` 显式写了 `required: false`，其余可选参数没有写 `required` 字段；按 OpenAPI 3.0 的规则，缺省即非必填。[读过官方页面（2026-10-20）] 〔证据:S9 page〕

---

## 3. 响应结构

### 3.1 信封

- 响应没有额外包装层，顶层对象就是各个 response schema。[真实请求验证过]（`/laws`、`/law_data` 两个响应）／[读过官方页面（2026-10-20）]（其余） 〔证据:S4 samples〕〔证据:S9 page〕

| 端点 | 顶层字段 |
|---|---|
| E1 `/laws` | `total_count`、`count`、`next_offset`、`laws[]` |
| E2 `/law_revisions` | `law_info`、`revisions[]` |
| E3 `/law_data` | `attached_files_info`、`law_info`、`revision_info`、`law_full_text` |
| E4 `/keyword` | `total_count`、`sentence_count`、`next_offset`、`items[]` |
| 错误 | `code`、`message` |

- `laws[]` 的每一项有三个子对象：`law_info`、`revision_info`、`current_revision_info`。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- `revision_info` 与 `current_revision_info` 结构相同；前者是 `asof` 选中的那一版，后者与 `asof` 无关、始终是现在以前最新的那一版。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- `items[]` 的每一项有 `law_info`、`revision_info`、`sentences[]`；`sentences[]` 每项是 `position`（命中位置）与 `text`（条文内容片段）。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- `total_count` 是过滤前的总件数，`count` 是本次返回的件数；`/keyword` 的 `total_count` 是命中总数、`sentence_count` 是本次返回的 `sentences` 数。定义原文把 `sentence_count` 描述为“每个响应单位显示的 `sentences` 数总和”。[读过官方页面（2026-10-20）] 〔证据:S9 page〕

### 3.2 `law_info`（与履历无关）

`law_type`、`law_id`、`law_num`、`law_num_era`、`law_num_year`、`law_num_type`、`law_num_num`、`promulgation_date`。共 8 个字段。[读过官方页面（2026-10-20）] 〔证据:S9 page〕；真实响应里 8 个字段全部出现，`law_num_year` 是 JSON 数字（不是字符串），`law_num_num` 是带前导 0 的字符串（例：`065`、`127`、`188`、`016`）。[真实请求验证过] 〔证据:S4 samples〕〔证据:S9 page〕

### 3.3 `revision_info`（与履历有关）

`law_revision_id`、`law_type`、`law_title`、`law_title_kana`、`abbrev`、`category`、`updated`、`amendment_promulgate_date`、`amendment_enforcement_date`、`amendment_enforcement_comment`、`amendment_scheduled_enforcement_date`、`amendment_law_id`、`amendment_law_title`、`amendment_law_title_kana`、`amendment_law_num`、`amendment_type`、`repeal_status`、`repeal_date`、`remain_in_force`、`mission`、`current_revision_status`。共 21 个字段。[读过官方页面（2026-10-20）] 〔证据:S9 page〕

- 真实样本里这 21 个字段全部出现；可空字段用 JSON `null`（例：原法的 `amendment_law_id` 为 `null`，另一样本里 `abbrev` 是空字符串 `""`）。[真实请求验证过] 〔证据:S4 samples〕〔证据:S9 page〕
- `amendment_type` 的枚举是字符串 `1`／`3`／`8`（定义里带引号）；`remain_in_force` 是布尔；`repeal_date` 可为 `null`。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- 定义文件自己的 `current_revision_info` 示例把 `repeal_status` 写成布尔 `false`，与枚举（字符串）不符——解析时遇到非字符串应当按“未知”处理，不要崩。[读过官方页面（2026-10-20）] 〔证据:S9 page〕

### 3.4 `law_full_text`（JSON 详细版）

- 结构是递归的：每个元素是 `{"tag": 元素名, "attr": {属性}, "children": [...]}`。[读过官方页面（2026-10-20）]（定义里的 XML 与 JSON 对应表）／[真实请求验证过]（两个真实样本） 〔证据:S9 page〕〔证据:S4 samples〕
- `children` 是混合数组：元素是字符串（文本）或对象（子元素）。真实样本里小样本有 55 个字符串子节点、大样本有 1,165 个；对象子节点分别是 131 与 2,451 个。[真实请求验证过] 〔证据:S4 samples〕〔证据:S9 page〕
- `attr` 在两个真实样本的 2,584 个元素里**全部是对象**（空对象 `{}` 或带属性）；没有出现字符串形式的 `attr`。解析器按对象处理即可，不要照抄“attr 可能是空字符串”的二手说法。[真实请求验证过] 〔证据:S4 samples〕〔证据:S9 page〕
- 根元素是 `Law`，根属性有 `Era`、`Lang`、`LawType`、`Num`、`Year`、`PromulgateMonth`、`PromulgateDay`。[真实请求验证过] 〔证据:S4 samples〕
- `json_format=light`（简易版）会把元素压成 `{"Article": [...]}` 这样的映射，并且只保留 `AmendLawNum`、`Extract`、`Paragraph` 的 `Num` 属性；内联元素（如 Ruby）会连同标签一起嵌进文本。要按条取属性（例如 `Article` 的 `Delete`、`Num`）就必须用 `full`。[读过官方页面（2026-10-20）] 〔证据:S9 page〕

### 3.5 元素名（JSON 的 `tag` 与 XML 的元素名）

- 定义文件有一张对应表，说明 XML 的标签与 JSON（详细版）的 `tag` 值是同一个字符串（表里只举了 `Sentence` 一个例子：XML `<Sentence Num="1" WritingMode="vertical">` 对应 `"tag": "Sentence"`）。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- 定义文件自带的 XML 示例（`law_data_response`）里出现的元素名，按层次列出：`law_data_response`、`attached_files_info`、`image_data`、`attached_files`、`attached_file`、`law_revision_id`、`src`、`updated`、`law_info`、`law_type`、`law_id`、`law_num`、`law_num_era`、`law_num_year`、`law_num_type`、`law_num_num`、`promulgation_date`、`revision_info`（以下同名字段）、`law_full_text`、`Law`、`LawNum`、`LawBody`、`LawTitle`、`MainProvision`、`Article`、`ArticleCaption`、`ArticleTitle`、`Paragraph`、`ParagraphNum`、`ParagraphSentence`、`Sentence`、`SupplProvision`、`SupplProvisionLabel`、`ParagraphCaption`、`AppdxNote`、`AppdxNoteTitle`、`RelatedArticleNum`、`TableStruct`、`TableStructTitle`、`Table`、`TableRow`、`TableColumn`、`Item`、`ItemTitle`、`ItemSentence`、`List`、`ListSentence`、`Column`、`Sublist1`、`Sublist1Sentence`、`FigStruct`、`Fig`、`Ruby`、`Rt`。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- 定义文件的 `elm` 表另外列出一批“法令 XML 要素”：`LawNum`、`LawTitle`、`EnactStatement`、`TOC`、`Preamble`、`MainProvision`、`Part`、`Chapter`、`Section`、`Subsection`、`Division`、`Article`、`Paragraph`、`Item`、`Subitem1`、`SupplProvision`、`AppdxTable`、`AppdxStyle`、`AppdxFormat`、`Appdx`、`AppdxFig`。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- 真实 JSON 样本（測量法，2026-10-20 抓取）里出现 34 种 `tag`，按出现次数：`Sentence` 476、`Paragraph` 279、`ParagraphNum` 279、`ParagraphSentence` 279、`Article` 168、`ArticleTitle` 168、`ArticleCaption` 154、`Item` 137、`ItemTitle` 137、`ItemSentence` 137、`SupplProvisionLabel` 34、`Column` 34、`SupplProvision` 33、`SectionTitle` 20、`ChapterTitle` 16、`ArticleRange` 14、`ParagraphCaption` 11、`TOCSection` 10、`Section` 10、`TOCChapter` 8、`Chapter` 8、`Ruby` 7、`Rt` 7、`Subitem1` 6、`Subitem1Title` 6、`Subitem1Sentence` 6、`Law` 1、`LawNum` 1、`LawBody` 1、`LawTitle` 1、`TOC` 1、`TOCLabel` 1、`TOCSupplProvision` 1、`MainProvision` 1。[真实请求验证过] 〔证据:S9 page〕
- 因此：条的层次是 `Law` > `LawBody` > `MainProvision` >（`Part`／`Chapter`／`Section`／…）> `Article` > `ArticleCaption`＋`ArticleTitle`＋`Paragraph` > `ParagraphNum`＋`ParagraphSentence` > `Sentence`；项的层次是 `Article` > `Paragraph` > `Item` > `ItemSentence`／`ItemTitle`，再往下有 `Subitem1`；附则有 `SupplProvision`／`SupplProvisionLabel`；别记有 `AppdxNote`／`AppdxNoteTitle`；表格有 `TableStruct`／`Table`／`TableRow`／`TableColumn`；附图有 `FigStruct`／`Fig`。[读过官方页面（2026-10-20）]＋[真实请求验证过]（样本里实际出现的部分） 〔证据:S9 page〕〔证据:S4 samples〕
- `Article` 的属性里有 `Delete` 与 `Hide`（真实样本里都是字符串 `"false"`）；被删除或被隐藏的条仍可能留在树里，取值可能是 `"true"`。[读过官方页面（2026-10-20）]（定义示例）／[真实请求验证过]（`Delete`／`Hide` 属性存在且为字符串） 〔证据:S9 page〕〔证据:S4 samples〕
- XML 响应本次**没有**真实抓取过，所以：XML 的 XML 声明（编码）、命名空间、空白与换行的处理都没有实测证据 → UNVERIFIED。[仅读文档] 〔证据:S9 page〕

### 3.6 Base64 规则

- 如果 `response_format` 与 `law_full_text_format` 不一致（一个 json、一个 xml），`law_full_text` 的返回值会被 Base64 编码，必须自行解码。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- 最省事的两种组合：两者都给 `json`（默认），或两者都给 `xml`。[读过官方页面（2026-10-20）] 〔证据:S9 page〕

### 3.7 标识符的形态

- `law_id`：真实样本里出现的 7 个全部是 15 个字符——`411AC0000000127`、`429AC0000000065`、`429CO0000000273`、`429M60000100117`、`324AC0000000188`、`324CO0000000322`、`324M50004000016`；定义文件自己的示例是 `322CO0000000016`、`405AC0000000088`。[真实请求验证过]（样本）＋[读过官方页面（2026-10-20）]（定义示例） 〔证据:S4 samples〕〔证据:S9 page〕
- `law_id` 各段分别代表什么（年号、种别、号数）：定义文件没有写 → NOT_FOUND。不要按位置去解析；判断法令种别请用 `law_info.law_type`／`law_num_type`。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- `law_num`：汉字形式的法令番号，例：`平成十一年法律第百二十七号`、`平成二十九年法律第六十五号`、`昭和二十四年政令第三百二十二号`、`昭和二十四年法律第百八十八号`；可以直接当 E2／E3／E6 的路径参数用。[真实请求验证过] 〔证据:S4 samples〕〔证据:S9 page〕
- `law_revision_id`：40 个字符，形式是 `law_id` ＋ `_` ＋ 8 位日期 ＋ `_` ＋ 15 位（改正法令的 ID，或 15 个 0）；例：`411AC0000000127_19990813_000000000000000`（原法，后缀全 0）、`429AC0000000065_20250601_504AC0000000068`、`324AC0000000188_20260903_508AC0000000027`；定义文件里的附件示例还给了 `322M40000010094_20240601_506M60000010035`。[真实请求验证过]（样本）＋[读过官方页面（2026-10-20）]（定义示例） 〔证据:S4 samples〕〔证据:S9 page〕
- 观察到的规律：`law_revision_id` 中间的 8 位日期与同一条 `revision_info.amendment_enforcement_date` 一致（原法那一条两者都是 1999-08-13）。[真实请求验证过] 〔证据:S4 samples〕〔证据:S9 page〕
- 各端点接受的标识符不同：E3 三种都收；E2 只收 `law_id` 或 `law_num`；E5 只收 `law_revision_id`；E6 三种都收。[读过官方页面（2026-10-20）] 〔证据:S9 page〕

---

## 4. 分页与上限

| 端点 | 页大小 | 游标 | 结束条件 |
|---|---|---|---|
| E1 `/laws` | `limit`，默认 100，**定义没写最大值** | `next_offset` → 下一次的 `offset` | `next_offset` 为 `null`（实测是**字段消失**，见 8.1） |
| E4 `/keyword` | `limit`，默认 100，上限 1000（单位是 `position` 数总和） | `next_offset` | 同上 |
| E2 `/law_revisions` | 无分页参数 | — | 一次返回全部履历 |
| E3 `/law_data` | 无分页 | — | 一次返回一部法令 |

- 上表的默认值与上限逐字取自定义文件。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- 2026-10-19 的真实请求 `law_title=住宅宿泊事業法&limit=3` 返回 `total_count` 6、`count` 3、`next_offset` 3——说明 `count` 等于 `limit` 时后面还有数据，必须按 `next_offset` 继续取。[真实请求验证过] 〔证据:S4 samples〕
- 2026-10-20 的真实请求 `law_title=測量法&limit=5` 返回 `total_count` 3、`count` 3，响应里**没有** `next_offset` 这个键（不是 `null`，是整个字段不出现）。解析时要把“缺失”与“null”都当成末页。[真实请求验证过] 〔证据:S9 page〕
- 不要用 `offset = offset + count` 自己推算下一页，用服务返回的 `next_offset`。[读过官方页面（2026-10-20）] 〔证据:S9 page〕

---

## 5. 日期字段、`当前文本` 与未施行

### 5.1 可用的日期字段

| 字段 | 定义里的含义 | 能不能当“数据日期” |
|---|---|---|
| `law_info.promulgation_date` | 该法令的公布日 | 能表示法律本身的成立日，不随改正变化；不能表示“这段文本的时点” |
| `revision_info.law_revision_id` | 履历 ID，中间 8 位是日期 | 真实样本里该日期段与 `amendment_enforcement_date` 一致；可用来固定一版 |
| `revision_info.updated` | 「正誤等による更新日時」——记录因勘误等被更新的时间 | **不能**当法律日期，它只说明记录何时被维护 |
| `revision_info.amendment_promulgate_date` | 改正法令的公布日 | 能表示“最近一次改正的公布日” |
| `revision_info.amendment_enforcement_date` | 「改正法令施行期日（この履歴に対応する改正の施行期日）」 | **最适合**表示“这段文本从哪天起施行” |
| `revision_info.amendment_enforcement_comment` | 施行期日规定的参考信息（例：公布の日から起算して一年を超えない範囲内において政令で定める日） | 不能当具体日期，只是在施行日由政令决定时给说明 |
| `revision_info.amendment_scheduled_enforcement_date` | 「擬似的な施行期日（実際の施行期日とは限らない）」 | **不能**当正式施行日 |
| `revision_info.repeal_date` | 废止日；`Expire`／`LossOfEffectiveness` 时是数据废止处理日 | 只在废止相关判断里用 |
| `revision_info.current_revision_status` | 履历状态：`CurrentEnforced`（现施行）／`UnEnforced`（未施行）／`PreviousEnforced`（过去施行）／`Repeal`（废止） | 能标记这一版是否已施行 |
| 查询参数 `asof` | 指定时点以前最近一次履历 | 能表达“截止某日”的文本 |

上表“定义里的含义”一列逐字或逐意取自定义文件，`updated`、`amendment_enforcement_date`、`amendment_scheduled_enforcement_date` 三处带引号的部分是原文。[读过官方页面（2026-10-20）] 〔证据:S9 page〕

- 真实样本可以互证：`law_revision_id` 的日期段与 `amendment_enforcement_date` 一致（例：`429AC0000000065_20250601_504AC0000000068` 对应 `2025-06-01`；`324AC0000000188_20260903_508AC0000000027` 对应 `2026-09-03`），原法那一条两者同为 1999-08-13。[真实请求验证过] 〔证据:S4 samples〕〔证据:S9 page〕
- `revision_info.updated` 与施行日不是一回事：測量法那一条的 `updated` 是 `2026-06-03T12:09:31+09:00`，而 `amendment_enforcement_date` 是 `2026-09-03`；把 `updated` 当“法律更新日”会提前近三个月。[真实请求验证过] 〔证据:S9 page〕

### 5.2 `当前文本` 在 API 里是什么意思

- 按法令 ID 或法令番号取正文时，定义原文写的是「現時点で最新のリビジョンの本文を取得します」；按履历 ID 取时，取的是该履历对应的正文。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- 也就是说，“当前”在定义里是“现在以前最新的那一版”，而不是“现在施行的那一版”——这两个含义在有没有未施行改正时会分叉。[推测]（依据：`asof` 的定义只按时间取最近履历，`current_revision_status` 另有 `UnEnforced` 一档，说明“最新”与“现施行”不是同一个维度。）
- 反过来，`/laws` 的 `revision_info` 是按 `asof` 选的，`current_revision_info` 一定是最新的一版，与 `asof` 无关。[读过官方页面（2026-10-20）] 〔证据:S9 page〕

### 5.3 未施行（已公布、尚未生效）的改正，API 给到什么

- 定义里的 `current_revision_status` 枚举包含 `UnEnforced`，说明文字是「未施行法令」——也就是说 API 确实能标出“已公布但尚未施行”的履历。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- `current_revision_status` 还可以当 `/law_revisions` 的过滤参数，用来只取某一类状态的履历；定义原文注明这个判断“与 `asof` 的值无关，始终与现在的状态比较”。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- `amendment_scheduled_enforcement_date` 提供的是「擬似的な施行期日（実際の施行期日とは限らない）」，不能直接当施行日。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- `asof` 可以把“某日为止的最新履历”固定下来，`/law_revisions` 可以取到一部法令的全部履历（含各版的施行日与状态）。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- 真实样本里**没有** `UnEnforced` 的实例：2026-10-19 的 3 条与 2026-10-20 的 3 条，`current_revision_status` 全是 `CurrentEnforced`，施行日都在抓取日之前。[真实请求验证过] 〔证据:S4 samples〕〔证据:S9 page〕
- **证据没有回答的问题**：按法令 ID 取“当前”正文时，如果该法令有一条已公布但未施行的改正，返回的是已施行的文本还是未施行的文本？定义只写“现在以前最新的那一版”，没有写是否按施行状态筛选；`/law_data` 的字段说明、`/laws` 的 `asof` 说明、两个真实样本都没有这种例子。→ UNVERIFIED（查证位置：定义文件 `/law_data` 的 description 与 `revision_info` 字段说明、`/laws` 的 `asof` 参数说明、两个真实 `law_data` 响应）。 [仅读文档] 〔证据:S9 page / S4 samples〕
- 能确定的是：要回答“截止某日的施行状态”，证据里现成的材料是 `asof` 参数、`amendment_enforcement_date`、`current_revision_status`（含 `UnEnforced`）与 `/law_revisions` 的全履历；要把“这一版是否已施行”写进答案，需要采集时把 `current_revision_status` 与 `amendment_enforcement_date` 一起存下来。[推测]（依据：上述字段的语义；未做真实请求验证。）

---

## 6. 字符编码

- JSON 响应是 UTF-8，日文原样输出，不是 `\uXXXX` 转义；2026-10-19 与 2026-10-20 的四个真实响应都是这样。[真实请求验证过] 〔证据:S4 samples〕〔证据:S9 page〕
- 真实响应的 Content-Type 是 `application/json`，**没有** `charset` 参数；不要等一个 charset 才解码，按 UTF-8 读。[真实请求验证过] 〔证据:S4 samples〕〔证据:S9 page〕
- 定义文件本身是 UTF-8 文本（含大量日文），但以 `application/x-www-form-urlencoded` 返回。[真实请求验证过] 〔证据:S6 page〕〔证据:S9 page〕
- XML 响应的编码声明：定义文件里的 XML 示例直接从 `<law_data_response>` 开始，**没有** XML 声明；真实 XML 响应本次没有抓取过。→ NOT_FOUND／UNVERIFIED。[仅读文档] 〔证据:S9 page〕
- 处理规则（来自 DATUMOJ 的参考资料提交规范）：样本保持原始字节，不重新编码、不重新排版；编码问题本身就是要测的内容。[仅读文档]

---

## 7. 错误形状与 HTTP 行为

- 定义文件为每个端点声明了 400 与 500 两种错误，正文都是 `error_info`：两个字符串字段 `code` 与 `message`。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- 定义里给的示例：`400004` 配「日付（asof等）が誤っています。」，`500001` 配「サーバ内処理で異常が発生しました。」；`error_info` 的 schema 自带示例是 `400001` 配「法令種別（law_type、law_num_type）が誤っています。」。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- XML 的错误正文形状是 `<error_info><code>…</code><message>…</message></error_info>`（定义示例）。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- 定义里**没有** 429、没有 `Retry-After`、没有任何限流数字，也没有写“找不到法令”时返回什么。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- 站点自己的前端脚本里有一个标题为「アクセスが集中しています」的页面组件，说明服务会出现拥塞状态；脚本里只有这个标题，没有任何阈值或重试说明。[仅读文档]（该文件是站点自己的前端脚本，2026-10-19 保存） 〔证据:S6 page〕
- 真实样本这一侧：四次 API 请求（两次 `law_data`、两次 `laws`，2026-10-19 与 2026-10-20 各两次）全部 HTTP 200、全部是合法 JSON，**没有触发过任何错误**；所以上表的错误形状只有文档证据，没有请求证据。[真实请求验证过]（只覆盖 200 这一面） 〔证据:S4 samples〕〔证据:S9 page〕
- 未知 ID、超长 URL、非法枚举值、重复参数的实际响应都没有测过 → UNVERIFIED。[推测]（依据：证据里没有对应请求。）

---

## 8. 已知坑

1. **`next_offset` 在末页会整个消失。** 2026-10-20 的 `/laws?law_title=測量法&limit=5` 响应里没有这个键；定义说它可为 `null`。解析时“缺失”和 `null` 都要当末页，不要写 `while next_offset is not None`。[真实请求验证过] 〔证据:S9 page〕
2. **`attached_files_info` 可能是 `null`。** 2026-10-20 的 `law_data`（測量法）响应里它就是 `null`；2026-10-19 的样本里它是对象。不要假设一定有 `attached_files` 数组。[真实请求验证过] 〔证据:S4 samples〕〔证据:S9 page〕
3. **`children` 是混合数组，`attr` 是对象。** 真实样本里 2,584 个元素的 `attr` 全是对象，1,220 个子节点是字符串；只按对象遍历会丢掉条文文本。[真实请求验证过] 〔证据:S4 samples〕〔证据:S9 page〕
4. **Base64 陷阱。** `response_format` 与 `law_full_text_format` 不一致时正文是 Base64，看起来像“正文变成乱码”。两者给同一个值即可。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
5. **`elm` 有两套写法。** 同一份定义里，参数说明给的是 `MainProvision-Paragraph[1]`，`elm` 的 schema 表给的是 `MainProvision-Paragraph_1`；两处都是官方文字，无法判断哪个对。建议不用 `elm`，取全文后本地筛。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
6. **定义文件的 Content-Type 是 `application/x-www-form-urlencoded`。** 用 Content-Type 判断“拿到的是不是 YAML”会误判。[真实请求验证过] 〔证据:S6 page〕〔证据:S9 page〕
7. **大法可能报错。** 定义原文有警告：「法令本文のデータサイズが大きい場合、エラーが発生することがあります。」并建议直接用浏览器打开 URL 而不经 Swagger UI。实测 242,424 字节的 測量法 正常返回；更大的法令（例如民法、会社法）没有测过。[读过官方页面（2026-10-20）]（引文）／[真实请求验证过]（242,424 字节这一例） 〔证据:S9 page〕
8. **`law_title` 会命中略称，`law_id` 是部分一致。** 搜“住宅宿泊事業法”会同时返回施行令与施行规则（2026-10-19 的样本 `total_count` 是 6）；要按 `law_type` 与法令名完全相等自己排一遍，不要直接取第一条。[真实请求验证过] 〔证据:S4 samples〕〔证据:S9 page〕
9. **`/keyword` 的 `limit` 不是件数。** 它是 `sentences` 的 `position` 数总和的上限；`total_count` 是命中总数、`sentence_count` 是本次返回的句数。拿 `limit` 估算“能取多少部法令”会错。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
10. **`position` 的形态不统一。** schema 的示例是 `MainProvision-Article_21-Paragraph_3`，示例响应里的值是 `amendsupplprovision`；只能当“位置提示”，不能当引用编号用。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
11. **`text` 里带高亮标签。** 默认用 `<span>` 包住命中处；XML 响应里这些标签会被转义。要展示就得先剥标签或转义，绝不原样拼进答案。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
12. **XML 列表项的名字在 schema 与示例之间不一致。** schema 给 `laws`／`items`／`attached_files` 的 `xml.name` 都写 `item`，而 XML 示例里分别是 `<law>`、`<item>`、`<attached_file>`。解析 XML 时两种名字都要认。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
13. **批量下载的分类码要去 0。** `category_cd` 在 API 里是 `001`…`050`，但批量下载 URL 里要用去掉第 2、3 位 0 的形式（宪法写 `1`）。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
14. **枚举字段可能给出枚举外的值。** 定义自己的 `current_revision_info` 示例把 `repeal_status` 写成 `false`；遇到非字符串按未知处理。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
15. **首页与说明页取不到规格。** `/`、`/apitop/`、`/help/` 都是同一个 800 字节前端外壳；`/api/2/` 返回 200 但正文 0 字节。规格只认定义文件。[真实请求验证过] 〔证据:S6 page〕
16. **末页字段。** 实测取完最后一页的响应里，next_offset 字段整个不出现。空结果本身没有样本，空结果响应的字段行为为 UNVERIFIED。[真实请求验证过] 〔证据:S9 page〕

---

## 9. 如何自己取得一份真实样本

请求形状（占位符用 `<>` 标出，全部为 GET，无需任何认证头）： [仅读文档] 〔证据:S9 page〕

```
https://laws.e-gov.go.jp/api/2/laws?law_title=<URL 编码后的法令名>&limit=<1..>
https://laws.e-gov.go.jp/api/2/law_revisions/<law_id 或 法令番号>
https://laws.e-gov.go.jp/api/2/law_data/<law_id 或 法令番号 或 law_revision_id>?response_format=json
https://laws.e-gov.go.jp/api/2/keyword?keyword=<URL 编码后的检索词>&limit=<n>
https://laws.e-gov.go.jp/api/2/law_file/xml/<law_id>
```

- 这五种形状都来自定义文件的 `paths` 与参数表；两种真实请求用过的形状是 `/laws?law_title=…&limit=…` 与 `/law_data/<law_id>?response_format=json`。[读过官方页面（2026-10-20）]＋[真实请求验证过] 〔证据:S9 page〕〔证据:S4 samples〕
- 速率：保存的真实请求样本采用约每秒 1 个请求、串行发送；证据里没有官方公布的数字，这个速率是样本获取时的用法，不是官方要求。[真实请求验证过]（样本请求方式）／[读过官方页面（2026-10-20）]（未见官方数字） 〔证据:S4 samples〕〔证据:S9 page〕
- 建议步骤是取样说明中的操作示例，不是官方要求。[仅读文档] 〔证据:S3 plan〕：
  1. 选一部短法（例如只有两条的 国旗及び国歌に関する法律）先取一次，确认响应大小与字段齐不齐。 [仅读文档] 〔证据:S3 plan〕
  2. 按**原始字节**存盘：不转码、不重新排版、不 pretty-print。 [仅读文档] 〔证据:S3 plan〕
  3. 记录请求 URL（本 API 没有 Key，URL 里没有要删的凭据）、抓取时间、HTTP 状态、Content-Type、字节数。 [仅读文档] 〔证据:S3 plan〕
  4. 要 XML 就用 `law_file/xml/<id>`（纯 XML 文件）或 `response_format=xml&law_full_text_format=xml`；不要混用两种格式，否则会拿到 Base64。 [仅读文档] 〔证据:S3 plan〕
  5. 提交前先确认许可允许再分发（本资料的判断见 `terms.md`），并在 `SAMPLES.md` 与 `SHA256SUMS` 里登记。 [仅读文档] 〔证据:S3 plan〕
- 实测响应大小参考：`law_data` 国旗国歌法 10,797 字节；`laws?law_title=住宅宿泊事業法&limit=3` 6,414 字节；`laws?law_title=測量法&limit=5` 5,639 字节；`law_data` 測量法 242,424 字节。[真实请求验证过] 〔证据:S4 samples〕〔证据:S9 page〕
- 整批取数的官方替代路线是 1.3 的三个 `bulkdownload` URL；增量窗口是过去三个月。[读过官方页面（2026-10-20）] 〔证据:S9 page〕

---

## 10. 与 DATUMOJ 计划的对照

DATUMOJ 计划关于本来源的陈述（任务给出的原文，只用于对照，不是本资料的依据）：

**陈述 A**：H1 会做约 30 次真实请求，按 1 请求/秒：法令一览 2 页，加约 25 部法令的当前全文（XML）与履历信息。 [仅读文档] 〔证据:S3 plan〕

- 一致的部分：定义里确实有法令一览（`/laws`，带 `limit`/`offset`/`next_offset`）、单法正文（`/law_data`）与履历（`/law_revisions`）三条路；保存样本的请求频率约为每秒 1 次。[读过官方页面（2026-10-20）]＋[真实请求验证过] 〔证据:S9 page〕〔证据:S4 samples〕
- 差异一（数量自洽性）：如果“履历信息”直接取 `/law_data` 响应里自带的 `revision_info`，那么 2 页列表 + 25 次正文 = 27 次请求，与“约 30 次”相合；如果另外逐法调 `/law_revisions`，25 部法就要 2 + 25 + 25 = 52 次。两条路都成立，但要在计划里写清走哪条。[推测]（依据：算术与 `/law_data` 的响应字段。）
- 差异二（“2 页”能覆盖多少）：`/laws` 的 `limit` 默认 100，定义没有写最大值；证据里没有法令总数，也没有一次 `/laws` 全量响应的样本。所以“2 页 = 全法令一览”无法确认 → UNVERIFIED。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- 差异三（“current full text as XML”怎么取）：有三条路——`response_format=xml`（整个响应是 XML）、`law_full_text_format=xml`（JSON 信封里正文是 XML）、`/law_file/xml/<id>`（纯 XML 文件，二进制下载）；前两条要注意 Base64 规则，第三条最省事但要处理二进制。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- 差异四（“current”的含义）：定义里的“当前”是“现在以前最新的一版”，不是“现在施行的文本”，两者在有未施行改正时会不同（见 5.2、5.3）。[推测]（依据：字段语义＋`UnEnforced` 枚举的存在。）

**陈述 B**：P0 的采集器 `jp-egov-law` 使用 API v2。 [仅读文档] 〔证据:S3 plan〕

- 一致：定义文件就是 Version 2（`info.version` 2.1.139），基础路径 `/api/2`，6 个端点。[读过官方页面（2026-10-20）] 〔证据:S9 page〕

**陈述 C**：法令全量约 9000 部，按 1 请求/秒约 3 小时。 [仅读文档] 〔证据:S3 plan〕

- 算术上自洽：9000 次请求按 1 次/秒是 2.5 小时的纯请求时间，加上响应耗时与重试，与“约 3 小时”同一量级。[推测]（依据：算术；证据里没有 9000 这个数字的官方出处。）
- 但“全量”如果指逐部取正文，数据量无法从件数推算：实测两部法令的正文响应相差 22 倍（10,797 与 242,424 字节），平均大小证据里没有 → 总量 UNVERIFIED。[真实请求验证过]（两个大小） 〔证据:S4 samples〕〔证据:S9 page〕
- 官方给了整批下载（全部／按分类／按更新日）这条非逐请求的路；它的压缩包内容与编码没有验证过。[读过官方页面（2026-10-20）] 〔证据:S9 page〕

**陈述 D**：评审要求“当前”的答案要说明依据的是哪一天的施行状态，并标出尚未施行的改正。

- API 侧现成的材料：`asof`（截到某日）、`amendment_enforcement_date`（这一版的施行日）、`current_revision_status` 含 `UnEnforced`（未施行）、`amendment_scheduled_enforcement_date`（拟制施行日，不能当正式施行日）、`/law_revisions` 的全履历。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- 不能确认的是：按法令 ID 取的“当前”正文是否已经把未施行改正算进去（5.3 的 UNVERIFIED）。这一点决定“标出未施行”是读一个字段就够，还是必须再比对 `/law_revisions`。[推测]（依据：定义没有写筛选规则。）
- 事实层面：把 `revision_info.amendment_enforcement_date` 与 `current_revision_status` 随正文一起存下来，是证据支持的做法；单看 `updated` 会把记录维护时间当成法律时间（见 5.1 的实测反例）。[推测]＋[真实请求验证过]（反例） 〔证据:S9 page〕

**陈述 E**：损坏的 Base64 样本与恶意 XML 样本由 datumoj 自己做（不是本资料的任务）。 [仅读文档] 〔证据:S3 plan〕

- Base64 的触发条件在定义里写清楚了（两种格式不一致时），所以这类样本可以按定义构造。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- 定义文件对 XML 的安全性（外部实体、实体膨胀、超大嵌套）**没有任何说明** → NOT_FOUND；datumoj 自造恶意 XML 样本时，期望行为要由 datumoj 自己定义。[读过官方页面（2026-10-20）] 〔证据:S9 page〕
- 本资料不含任何自造样本。[仅读文档]

---

## 11. 验证程度表

| # | 陈述 / 陈述组 | 验证程度 | 证据 |
|---|---|---|---|
| 1 | 基础 URL、6 个端点的路径与 operationId | 读过官方页面（2026-10-20） | S9 page |
| 2 | 只有 GET；没有 `securitySchemes`／`security` | 读过官方页面（2026-10-20） | S9 page |
| 3 | 无认证头也能取到数据（4 次 API 请求全部 200） | 真实请求验证过 | S4 samples / S9 page |
| 4 | 定义文件 `info.version` 2.1.139、125,750 字节、两天同 sha256 | 读过官方页面（2026-10-20）／真实请求验证过 | S6 page / S9 page |
| 5 | 定义文件以 `application/x-www-form-urlencoded` 返回 | 真实请求验证过 | S6 page / S9 page |
| 6 | `/`、`/apitop/`、`/help/` 是 800 字节前端外壳；`/api/2/` 是 200 + 0 字节 | 真实请求验证过 | S6 page |
| 7 | E1 的 21 个参数、E2 的 22 个、E3 的 8 个、E4 的 18 个、E5 的 2 个、E6 的 3 个，名字/类型/默认值 | 读过官方页面（2026-10-20） | S9 page（与 S2 spec 一致） |
| 8 | `/laws` 无必填参数；`/keyword` 的 `keyword` 必填 | 读过官方页面（2026-10-20） | S9 page |
| 9 | `limit` 默认 100、`offset` 默认 0；`/keyword` 上限 1000 且单位是 `position` 数总和 | 读过官方页面（2026-10-20） | S9 page |
| 10 | `/laws` 的 `limit` 没有写最大值 | 读过官方页面（2026-10-20） | S9 page |
| 11 | `/law_revisions` 无分页参数、返回全部履历、由新到旧 | 读过官方页面（2026-10-20） | S9 page |
| 12 | 响应顶层字段与 `law_info`（8 字段）、`revision_info`（21 字段）的名字 | 读过官方页面（2026-10-20）＋真实请求验证过 | S9 page / S4 samples |
| 13 | `law_num_year` 是数字、`law_num_num` 是带前导 0 的字符串 | 真实请求验证过 | S4 samples / S9 page |
| 14 | `next_offset` 在末页整个字段消失 | 真实请求验证过 | S9 page |
| 15 | `attached_files_info` 可以为 `null` | 真实请求验证过 | S9 page |
| 16 | `law_full_text` 的 `tag`/`attr`/`children` 结构；`attr` 全为对象、`children` 混合字符串与对象 | 真实请求验证过 | S4 samples / S9 page |
| 17 | 34 种 `tag` 的清单与计数（測量法样本） | 真实请求验证过 | S9 page |
| 18 | XML 元素名清单（定义自带示例与 `elm` 表） | 读过官方页面（2026-10-20） | S9 page |
| 19 | 真实 XML 响应的编码声明、命名空间、空白处理 | 仅读文档（未抓取 XML）→ NOT_FOUND | S9 page |
| 20 | Base64 规则（两种格式不一致时） | 读过官方页面（2026-10-20） | S9 page |
| 21 | `json_format=light` 只保留 `AmendLawNum`、`Extract`、`Paragraph.Num` | 读过官方页面（2026-10-20） | S9 page |
| 22 | 日期字段的含义；`updated` 不是法律日期；`amendment_scheduled_enforcement_date` 不是正式施行日 | 读过官方页面（2026-10-20）＋真实请求验证过（`updated` 与施行日不同的实例） | S9 page |
| 23 | `law_revision_id` 的日期段与 `amendment_enforcement_date` 一致 | 真实请求验证过 | S4 samples / S9 page |
| 24 | `current_revision_status` 含 `UnEnforced`（未施行法令）；该参数按现在比较、与 `asof` 无关 | 读过官方页面（2026-10-20） | S9 page |
| 25 | 按 `law_id` 取“当前”时是否包含未施行改正 | 推测 → UNVERIFIED | S9 page（定义未写） |
| 26 | 错误体 `error_info{code,message}`、400/500、示例码 400004/500001/400001 | 读过官方页面（2026-10-20） | S9 page |
| 27 | 没有 429、没有 Retry-After、没有限流数字 | 读过官方页面（2026-10-20） | S9 page |
| 28 | 站点前端脚本里有拥塞提示、没有阈值 | 仅读文档（站点自己的前端脚本） | S6 page |
| 29 | 真实请求全部 200，未触发任何错误 | 真实请求验证过 | S4 samples / S9 page |
| 30 | 批量下载三种 URL、分类码去 0、三个月增量窗口 | 读过官方页面（2026-10-20） | S9 page / S7 addendum |
| 31 | 批量下载的压缩包内容/编码未验证 | 仅读文档 → UNVERIFIED | S7 addendum |
| 32 | 字符编码：JSON 为 UTF-8 原文，Content-Type 无 charset | 真实请求验证过 | S4 samples / S9 page |
| 33 | `elm` 两套写法（`[1]` 与 `_1`） | 读过官方页面（2026-10-20） | S9 page |
| 34 | 大法可能报错（原文警告）；242,424 字节的一例正常 | 读过官方页面（2026-10-20）＋真实请求验证过 | S9 page |
| 35 | 未知 ID／非法枚举／重复参数的实际响应 | 推测 → UNVERIFIED | 无对应请求 |
| 36 | robots.txt 对本站返回前端外壳 | 真实请求验证过 | S8 robots / S6 page |

---

## 12. 本文件里仍未确认的项

- 按 `law_id` 取“当前”正文时，是否包含已公布未施行的改正：UNVERIFIED。查了定义文件 `/law_data` 的 description、`revision_info` 的字段说明、`/laws` 的 `asof` 说明，以及两个真实 `law_data` 响应（都没有 `UnEnforced` 实例）。[仅读文档]＋[真实请求验证过]
- 真实 XML 响应的编码声明与命名空间：NOT_FOUND，本次没有抓取 XML。[仅读文档]
- `/laws` 的 `limit` 上限、以及一次能否取回全部约 9000 部法令：UNVERIFIED，定义没有写最大值。[读过官方页面（2026-10-20）]
- 未知 ID、非法枚举、重复参数、空结果的确切响应：UNVERIFIED，没有对应请求。[推测]
- 批量下载的压缩包内容、文件编码、目录结构：UNVERIFIED，只抄录了 URL。[仅读文档]
- 法令总数的官方数字：NOT_FOUND；“约 9000 部”是 DATUMOJ 计划里的说法，本资料没有找到官方出处。[读过官方页面（2026-10-20）]
