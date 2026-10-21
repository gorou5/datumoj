# e-Gov 法令API Version 2（jp-egov-law）

| 项目 | 内容 |
|---|---|
| 提供者 | zenithli（AI 辅助整理） |
| 资料整理日期 | 2026-10-20 |
| 资料核对日期 | 2026-10-20（法令API 的定义文件与 robots.txt 于当日直接读取）；利用规约页为 2026-10-19，公共データ利用規約（第1.0版）页面为 2026-10-20。已读取支持的事实沿用原日期，逐条列在 notes.md 的验证程度表里 |
| 官方入口 | e-Gov 法令検索：https://laws.e-gov.go.jp/ ；法令API Version 2 的定义文件（OpenAPI 3.0.3，`info.version` 2.1.139）：https://laws.e-gov.go.jp/api/2/swagger-ui/lawapi-v2.yaml |
| 需要 Key | 否。定义文件里没有任何 `securitySchemes`，任何 operation 也没有 `security` 要求；2026-10-19 与 2026-10-20 的真实请求都在不带认证头的情况下返回 HTTP 200。证据里没有申请流程可写（见 notes.md 第 1 节） |
| 许可 / 利用规约 | e-Govポータル 利用規約（https://www.e-gov.go.jp/terms）：除另有权利表记外，适用 公共データ利用規約（第1.0版）（PDL1.0，https://www.digital.go.jp/resources/open_data/public_data_license_v1.0）；可否再分发：可以（PDL1.0 明写可复制、公众传播、翻译与改编，并写明可以商用；依据、引文与判断过程见 terms.md 第 2、5、8 节） |
| 本资料的可信度 | 分开写：① [真实请求验证过]：端点应答、HTTP 200、`/laws` 与 `/law_data` 的顶层字段、`next_offset` 的实际行为、`law_full_text` 的 tag 结构、响应字节数。② [读过官方页面（2026-10-20）]：定义文件与 robots.txt、PDL1.0 页面（2026-10-20），以及 e-Gov 利用规约页（2026-10-19）。③ [仅读文档] 或 [推测]：参数语义、错误形状、`asof` 与未施行的关系、批量下载、字符编码的一部分，逐条标注于 notes.md 第 11 节。〔证据:S4，samples/SAMPLES.md〕〔证据:S6，https://www.e-gov.go.jp/terms〕〔证据:S8，https://laws.e-gov.go.jp/robots.txt〕〔证据:S9，https://www.digital.go.jp/resources/open_data/public_data_license_v1.0〕 |
| 对应的 DATUMOJ 任务 | DATUMOJ-IVY-201（e-Gov 法令采集器）、DATUMOJ-IVY-202（法令 XML 解析器：按条切分，带层级和 locator）；据 datumoj 维护者在 PR #2 评论中的说明两者已完成 [仅读文档] |

## 内容清单

- `README.md`：本文件。元信息表与本目录的清单。
- `notes.md`：端点与定义文件位置；6 个端点；每个参数的类型/必填/允许值与默认值；分页与上限；响应结构（信封、字段名、`law_full_text` 的 JSON 与 XML 形态）；日期字段与“当前文本”“未施行”；字符编码；错误与 HTTP 行为；已知坑；自行取得样本的请求形状与速率；“与 DATUMOJ 计划的对照”；验证程度表。 [读过官方页面（2026-10-20）] 〔证据:S9，https://laws.e-gov.go.jp/api/2/swagger-ui/lawapi-v2.yaml〕
- `terms.md`：利用规约与许可要点（适用范围、商用、署名、第三方权利、再分发、自动获取与负载、robots.txt 的观察事实、保留与更新）＋原文链接＋核对日期＋样本判断的逐条依据＋“对采集方案的直接影响”。只写摘要与短引用，不转载全文，不作法律结论。 [仅读文档] 〔证据:S9，https://www.digital.go.jp/resources/open_data/public_data_license_v1.0〕
- `samples/`：两个真实响应样本（`egov_law_data_flag_real.json`、`egov_laws_search_minpaku_title_real.json`），原始字节未改动；来源 URL、抓取时间、用途、字段说明与署名写法在 `samples/SAMPLES.md`，校验值在 `samples/SHA256SUMS`。**为什么这次收录样本**：2026-10-19 读到的 e-Gov 利用规约页写明除另有权利表记外适用 PDL1.0，2026-10-20 读到的 PDL1.0 正文写明可以复制、公众传播、翻译与改编并写明可以商用，因此再分发有明确依据；引文与推理见 `terms.md` 第 8 节。 [读过官方页面（2026-10-20）] 〔证据:S6，https://www.e-gov.go.jp/terms〕 〔证据:S9，https://www.digital.go.jp/resources/open_data/public_data_license_v1.0〕
- 本资料不含 `.meta.json` 文件、不含任何自造样本、不含合成数据；样本的元信息以 `SAMPLES.md` 的文字形式给出。
