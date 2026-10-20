# e-Gov 法令 API v2 测试样本

录制日期：2026-10-20（JST）。接口：`https://laws.e-gov.go.jp/api/2`（e-Gov 法令 API Version 2，规格 2.1.139）。
录制工具 User-Agent：`DATUMOJ-fixture-recorder/0.1 (+https://github.com/gorou5/datumoj)`，请求间隔 ≥1.1 秒，共 50 次请求（`Accept-Encoding: identity`）。

## 许可与署名

- 真实响应文件的内容来自 **e-Gov 法令検索 / 法令 API（デジタル庁）**，依照 e-Gov 法令検索的利用规约（以「政府標準利用規約（第2.0版）」为基础，与 CC BY 4.0 兼容）利用与再分发，须注明出典。
- 出典：e-Gov 法令検索（https://laws.e-gov.go.jp/），デジタル庁。本目录的样本**未经编辑**（原始响应字节）；`.synthetic` 文件是本项目人工构造的，不是政府数据，部分字段复制自真实响应后加以篡改。
- 法令本文本身（法律、政令）依日本著作権法第13条不是著作权的对象。

## 约定

- 所有真实样本均为 **原始响应字节**（未重新编码、未格式化）。JSON 外层；`law_data` 用 `response_format=json&law_full_text_format=xml`，所以 `law_full_text` 是 **base64 编码的 XML 字符串**（已实测）。
- `law_data` 统一显式传 `asof=2026-10-20`，取录制当日有效的版本。
- 抓取时间为请求发出时刻（JST, +09:00）。大小为文件字节数。
- `SHA256SUMS` 覆盖本目录除 `README.md` 与 `SHA256SUMS` 本身外的全部文件（`shasum -a 256 -c SHA256SUMS` 校验）。

## 文件一览

| 文件名 | 请求 URL | HTTP 状态 | 抓取时间 | 大小 (B) | 用途 |
| --- | --- | --- | --- | ---: | --- |
| `laws_list.limit10.offset0.json` | `https://laws.e-gov.go.jp/api/2/laws?limit=10&offset=0` | 200 | 2026-10-20T21:00:53+09:00 | 18477 | IVY-201 分页：连续页（next_offset 递进） |
| `laws_list.limit10.offset10.json` | `https://laws.e-gov.go.jp/api/2/laws?limit=10&offset=10` | 200 | 2026-10-20T21:00:55+09:00 | 18505 | IVY-201 分页：连续页（next_offset 递进） |
| `laws_list.limit10.offset9560.last.json` | `https://laws.e-gov.go.jp/api/2/laws?limit=10&offset=9560` | 200 | 2026-10-20T21:00:57+09:00 | 15647 | IVY-201 分页终止：最后一页（count=7，无 next_offset 键） |
| `laws_list.limit10.offset9570.empty.json` | `https://laws.e-gov.go.jp/api/2/laws?limit=10&offset=9570` | 200 | 2026-10-20T21:00:59+09:00 | 37 | IVY-201 分页终止：offset 超出末尾的空页（注意 total_count=0、无 next_offset 键） |
| `law_data.404.999AC0000000999.json` | `https://laws.e-gov.go.jp/api/2/law_data/999AC0000000999?response_format=json&law_full_text_format=xml` | 404 | 2026-10-20T21:01:01+09:00 | 120 | IVY-201 错误处理：law_data 不存在的法令 ID（错误码 404004） |
| `law_revisions.404.428AC0000000103.json` | `https://laws.e-gov.go.jp/api/2/law_revisions/428AC0000000103` | 404 | 2026-10-20T21:01:25+09:00 | 60 | IVY-201 错误处理：law_revisions 不存在的法令 ID（错误码 404001，实为 ID 写错：议员立法应为 AC1） |
| `law_data.321CONSTITUTION.asof2026-10-20.xml-in-json.json` | `https://laws.e-gov.go.jp/api/2/law_data/321CONSTITUTION?asof=2026-10-20&response_format=json&law_full_text_format=xml` | 200 | 2026-10-20T21:02:13+09:00 | 102494 | IVY-201 全文 / IVY-202 解析（章）/ IVY-702 出题 |
| `law_data.322AC0000000049.asof2026-10-20.xml-in-json.json` | `https://laws.e-gov.go.jp/api/2/law_data/322AC0000000049?asof=2026-10-20&response_format=json&law_full_text_format=xml` | 200 | 2026-10-20T21:02:16+09:00 | 532090 | IVY-201 全文 / IVY-202 解析（章、条の二、附则×59、别表×3）/ IVY-702 出题 |
| `law_data.330AC0000000179.asof2026-10-20.xml-in-json.json` | `https://laws.e-gov.go.jp/api/2/law_data/330AC0000000179?asof=2026-10-20&response_format=json&law_full_text_format=xml` | 200 | 2026-10-20T21:01:55+09:00 | 102159 | IVY-201 全文 / IVY-202 解析（章、条の二、附则×9）/ IVY-702 出题 |
| `law_data.331AC0000000120.asof2026-10-20.xml-in-json.json` | `https://laws.e-gov.go.jp/api/2/law_data/331AC0000000120?asof=2026-10-20&response_format=json&law_full_text_format=xml` | 200 | 2026-10-20T21:02:19+09:00 | 84658 | IVY-201 全文 / IVY-202 解析（附则×12）/ IVY-702 出题 |
| `law_data.338AC0000000154.asof2026-10-20.xml-in-json.json` | `https://laws.e-gov.go.jp/api/2/law_data/338AC0000000154?asof=2026-10-20&response_format=json&law_full_text_format=xml` | 200 | 2026-10-20T21:02:00+09:00 | 94666 | IVY-201 全文 / IVY-202 解析（章、节、附则×27）/ IVY-702 出题 |
| `law_data.363CO0000000360.asof2026-10-20.xml-in-json.json` | `https://laws.e-gov.go.jp/api/2/law_data/363CO0000000360?asof=2026-10-20&response_format=json&law_full_text_format=xml` | 200 | 2026-10-20T21:02:04+09:00 | 1718911 | IVY-201 全文 / IVY-202 解析（章、条の二、附则×143）/ IVY-702 出题 |
| `law_data.405AC0000000088.asof2026-10-20.xml-in-json.json` | `https://laws.e-gov.go.jp/api/2/law_data/405AC0000000088?asof=2026-10-20&response_format=json&law_full_text_format=xml` | 200 | 2026-10-20T21:01:54+09:00 | 190782 | IVY-201 全文 / IVY-202 解析（章、节、条の二、附则×17）/ IVY-702 出题 |
| `law_data.410AC0000000025.asof2026-10-20.xml-in-json.json` | `https://laws.e-gov.go.jp/api/2/law_data/410AC0000000025?asof=2026-10-20&response_format=json&law_full_text_format=xml` | 200 | 2026-10-20T21:02:18+09:00 | 100009 | IVY-201 全文 / IVY-202 解析（附则×14）/ IVY-702 出题 |
| `law_data.411AC0000000018.asof2026-10-20.xml-in-json.json` | `https://laws.e-gov.go.jp/api/2/law_data/411AC0000000018?asof=2026-10-20&response_format=json&law_full_text_format=xml` | 200 | 2026-10-20T21:02:20+09:00 | 546634 | IVY-201 全文 / IVY-202 解析（章、节、条の二、附则×45）/ IVY-702 出题 |
| `law_data.411AC0000000042.asof2026-10-20.xml-in-json.json` | `https://laws.e-gov.go.jp/api/2/law_data/411AC0000000042?asof=2026-10-20&response_format=json&law_full_text_format=xml` | 200 | 2026-10-20T21:02:02+09:00 | 126234 | IVY-201 全文 / IVY-202 解析（章、条の二、附则×16）/ IVY-702 出题 |
| `law_data.411AC0000000128.asof2026-10-20.xml-in-json.json` | `https://laws.e-gov.go.jp/api/2/law_data/411AC0000000128?asof=2026-10-20&response_format=json&law_full_text_format=xml` | 200 | 2026-10-20T21:02:11+09:00 | 45332 | IVY-201 全文 / IVY-202 解析（附则×5）/ IVY-702 出题 |
| `law_data.412AC0000000061.asof2026-10-20.xml-in-json.json` | `https://laws.e-gov.go.jp/api/2/law_data/412AC0000000061?asof=2026-10-20&response_format=json&law_full_text_format=xml` | 200 | 2026-10-20T21:02:15+09:00 | 309379 | IVY-201 全文 / IVY-202 解析（章、节、条の二、附则×23）/ IVY-702 出题 |
| `law_data.412AC0000000102.asof2026-10-20.xml-in-json.json` | `https://laws.e-gov.go.jp/api/2/law_data/412AC0000000102?asof=2026-10-20&response_format=json&law_full_text_format=xml` | 200 | 2026-10-20T21:01:59+09:00 | 123135 | IVY-201 全文 / IVY-202 解析（章、节、附则×6）/ IVY-702 出题 |
| `law_data.414AC0000000151.asof2026-10-20.xml-in-json.json` | `https://laws.e-gov.go.jp/api/2/law_data/414AC0000000151?asof=2026-10-20&response_format=json&law_full_text_format=xml` | 200 | 2026-10-20T21:02:10+09:00 | 227892 | IVY-201 全文 / IVY-202 解析（章、节、附则×51）/ IVY-702 出题 |
| `law_data.415AC0000000057.asof2026-10-20.xml-in-json.json` | `https://laws.e-gov.go.jp/api/2/law_data/415AC0000000057?asof=2026-10-20&response_format=json&law_full_text_format=xml` | 200 | 2026-10-20T21:01:53+09:00 | 733544 | IVY-201 全文 / IVY-202 解析（章、节、附则×22、别表×2）/ IVY-702 出题 |
| `law_data.416AC0000000122.asof2026-10-20.xml-in-json.json` | `https://laws.e-gov.go.jp/api/2/law_data/416AC0000000122?asof=2026-10-20&response_format=json&law_full_text_format=xml` | 200 | 2026-10-20T21:02:14+09:00 | 86317 | IVY-201 全文 / IVY-202 解析（章、附则×10、别表×1）/ IVY-702 出题 |
| `law_data.419AC0000000053.asof2026-10-20.xml-in-json.json` | `https://laws.e-gov.go.jp/api/2/law_data/419AC0000000053?asof=2026-10-20&response_format=json&law_full_text_format=xml` | 200 | 2026-10-20T21:01:57+09:00 | 214584 | IVY-201 全文 / IVY-202 解析（章、节、条の二、附则×6）/ IVY-702 出题 |
| `law_data.421AC0000000066.asof2026-10-20.xml-in-json.json` | `https://laws.e-gov.go.jp/api/2/law_data/421AC0000000066?asof=2026-10-20&response_format=json&law_full_text_format=xml` | 200 | 2026-10-20T21:01:58+09:00 | 172198 | IVY-201 全文 / IVY-202 解析（章、节、附则×17、别表×2）/ IVY-702 出题 |
| `law_data.425AC0000000027.asof2026-10-20.xml-in-json.json` | `https://laws.e-gov.go.jp/api/2/law_data/425AC0000000027?asof=2026-10-20&response_format=json&law_full_text_format=xml` | 200 | 2026-10-20T21:02:07+09:00 | 939578 | IVY-201 全文 / IVY-202 解析（章、节、条の二、附则×76、别表×1）/ IVY-702 出题 |
| `law_data.426AC0000000068.asof2026-10-20.xml-in-json.json` | `https://laws.e-gov.go.jp/api/2/law_data/426AC0000000068?asof=2026-10-20&response_format=json&law_full_text_format=xml` | 200 | 2026-10-20T21:02:06+09:00 | 411677 | IVY-201 全文 / IVY-202 解析（章、节、附则×7、别表×3）/ IVY-702 出题 |
| `law_data.428AC1000000103.asof2026-10-20.xml-in-json.json` | `https://laws.e-gov.go.jp/api/2/law_data/428AC1000000103?asof=2026-10-20&response_format=json&law_full_text_format=xml` | 200 | 2026-10-20T21:02:09+09:00 | 50407 | IVY-201 全文 / IVY-202 解析（章、附则×3）/ IVY-702 出题 |
| `law_data.503AC0000000035.asof2026-10-20.xml-in-json.json` | `https://laws.e-gov.go.jp/api/2/law_data/503AC0000000035?asof=2026-10-20&response_format=json&law_full_text_format=xml` | 200 | 2026-10-20T21:02:03+09:00 | 72761 | IVY-201 全文 / IVY-202 解析（章、附则×3）/ IVY-702 出题 |
| `law_revisions.321CONSTITUTION.json` | `https://laws.e-gov.go.jp/api/2/law_revisions/321CONSTITUTION` | 200 | 2026-10-20T21:01:29+09:00 | 892 | IVY-201 修订历史 |
| `law_revisions.322AC0000000049.json` | `https://laws.e-gov.go.jp/api/2/law_revisions/322AC0000000049` | 200 | 2026-10-20T21:01:32+09:00 | 12299 | IVY-201 修订历史；含 2 条未施行修改（施行日 > 2026-10-20） |
| `law_revisions.330AC0000000179.json` | `https://laws.e-gov.go.jp/api/2/law_revisions/330AC0000000179` | 200 | 2026-10-20T21:01:13+09:00 | 4168 | IVY-201 修订历史 |
| `law_revisions.331AC0000000120.json` | `https://laws.e-gov.go.jp/api/2/law_revisions/331AC0000000120` | 200 | 2026-10-20T21:01:35+09:00 | 3021 | IVY-201 修订历史 |
| `law_revisions.338AC0000000154.json` | `https://laws.e-gov.go.jp/api/2/law_revisions/338AC0000000154` | 200 | 2026-10-20T21:01:18+09:00 | 3598 | IVY-201 修订历史 |
| `law_revisions.363CO0000000360.json` | `https://laws.e-gov.go.jp/api/2/law_revisions/363CO0000000360` | 200 | 2026-10-20T21:01:22+09:00 | 42080 | IVY-201 修订历史；含 4 条未施行修改（施行日 > 2026-10-20） |
| `law_revisions.405AC0000000088.json` | `https://laws.e-gov.go.jp/api/2/law_revisions/405AC0000000088` | 200 | 2026-10-20T21:01:12+09:00 | 9126 | IVY-201 修订历史；含 1 条未施行修改（施行日 > 2026-10-20） |
| `law_revisions.410AC0000000025.json` | `https://laws.e-gov.go.jp/api/2/law_revisions/410AC0000000025` | 200 | 2026-10-20T21:01:34+09:00 | 9401 | IVY-201 修订历史；含 1 条未施行修改（施行日 > 2026-10-20） |
| `law_revisions.411AC0000000018.json` | `https://laws.e-gov.go.jp/api/2/law_revisions/411AC0000000018` | 200 | 2026-10-20T21:01:36+09:00 | 20554 | IVY-201 修订历史 |
| `law_revisions.411AC0000000042.json` | `https://laws.e-gov.go.jp/api/2/law_revisions/411AC0000000042` | 200 | 2026-10-20T21:01:19+09:00 | 6225 | IVY-201 修订历史；含 1 条未施行修改（施行日 > 2026-10-20） |
| `law_revisions.411AC0000000128.json` | `https://laws.e-gov.go.jp/api/2/law_revisions/411AC0000000128` | 200 | 2026-10-20T21:01:28+09:00 | 2939 | IVY-201 修订历史 |
| `law_revisions.412AC0000000061.json` | `https://laws.e-gov.go.jp/api/2/law_revisions/412AC0000000061` | 200 | 2026-10-20T21:01:31+09:00 | 15613 | IVY-201 修订历史；含 1 条未施行修改（施行日 > 2026-10-20） |
| `law_revisions.412AC0000000102.json` | `https://laws.e-gov.go.jp/api/2/law_revisions/412AC0000000102` | 200 | 2026-10-20T21:01:17+09:00 | 4561 | IVY-201 修订历史 |
| `law_revisions.414AC0000000151.json` | `https://laws.e-gov.go.jp/api/2/law_revisions/414AC0000000151` | 200 | 2026-10-20T21:01:26+09:00 | 22234 | IVY-201 修订历史；含 2 条未施行修改（施行日 > 2026-10-20） |
| `law_revisions.415AC0000000057.json` | `https://laws.e-gov.go.jp/api/2/law_revisions/415AC0000000057` | 200 | 2026-10-20T21:01:11+09:00 | 30535 | IVY-201 修订历史；含 6 条未施行修改（施行日 > 2026-10-20） |
| `law_revisions.416AC0000000122.json` | `https://laws.e-gov.go.jp/api/2/law_revisions/416AC0000000122` | 200 | 2026-10-20T21:01:30+09:00 | 7953 | IVY-201 修订历史；含 2 条未施行修改（施行日 > 2026-10-20） |
| `law_revisions.419AC0000000053.json` | `https://laws.e-gov.go.jp/api/2/law_revisions/419AC0000000053` | 200 | 2026-10-20T21:01:15+09:00 | 8884 | IVY-201 修订历史；含 2 条未施行修改（施行日 > 2026-10-20） |
| `law_revisions.421AC0000000066.json` | `https://laws.e-gov.go.jp/api/2/law_revisions/421AC0000000066` | 200 | 2026-10-20T21:01:16+09:00 | 12267 | IVY-201 修订历史；含 3 条未施行修改（施行日 > 2026-10-20）；**无 CurrentEnforced 条目**（见“已知异常”） |
| `law_revisions.425AC0000000027.json` | `https://laws.e-gov.go.jp/api/2/law_revisions/425AC0000000027` | 200 | 2026-10-20T21:01:24+09:00 | 109701 | IVY-201 修订历史；含 6 条未施行修改（施行日 > 2026-10-20）；**无 CurrentEnforced 条目**（见“已知异常”） |
| `law_revisions.426AC0000000068.json` | `https://laws.e-gov.go.jp/api/2/law_revisions/426AC0000000068` | 200 | 2026-10-20T21:01:23+09:00 | 11076 | IVY-201 修订历史；含 1 条未施行修改（施行日 > 2026-10-20） |
| `law_revisions.428AC1000000103.json` | `https://laws.e-gov.go.jp/api/2/law_revisions/428AC1000000103` | 200 | 2026-10-20T21:01:51+09:00 | 2728 | IVY-201 修订历史 |
| `law_revisions.503AC0000000035.json` | `https://laws.e-gov.go.jp/api/2/law_revisions/503AC0000000035` | 200 | 2026-10-20T21:01:21+09:00 | 3731 | IVY-201 修订历史 |
| `law_data.synthetic.bad-base64.json` | （人工构造） | — | 2026-10-20（生成） | 1273 | IVY-201/202：law_full_text 的 base64 损坏（混入非法字符、长度非 4 的倍数），严格解码必须报错 |
| `law_data.synthetic.entity-attack.json` | （人工构造） | — | 2026-10-20（生成） | 3091 | IVY-202 XML 安全：law_full_text 为下列恶意 XML 的 base64，解析器必须拒绝 DTD/实体 |
| `law_full_text.synthetic.entity-attack.xml` | （人工构造） | — | 2026-10-20（生成） | 1521 | IVY-202 XML 安全：含 `<!DOCTYPE … <!ENTITY …>`（外部实体 file:///etc/passwd、http 外部实体、十亿笑嵌套实体） |

## 法令一览

“未施行修改”取自对应 `law_revisions` 中 `current_revision_status=UnEnforced` 且施行日晚于 2026-10-20 的条目。条数统计基于 `asof=2026-10-20` 全文：章/节为 MainProvision 内 Chapter/Section 数，“条の二”为 Article@Num 含 `_` 的条数，附则为 SupplProvision 总数（其中带 AmendLawNum 的是改正附则），别表为 AppdxTable 数。

| 法令 ID | 法令番号 | 法令名 | 返回版本 (law_revision_id) | 章/节 | 条数 | 条の二 | 附则（改正附则） | 别表 | 未施行修改（施行日） |
| --- | --- | --- | --- | --- | ---: | --- | --- | ---: | --- |
| `321CONSTITUTION` | 昭和二十一年憲法 | 日本国憲法 | `321CONSTITUTION_19470503_000000000000000` | 11/0 | 103 | 无 | 0（0） | 0 | 无 |
| `322AC0000000049` | 昭和二十二年法律第四十九号 | 労働基準法 | `322AC0000000049_20260717_508AC0000000060` | 14/0 | 124 | 有 17（例 第三十二条の二） | 59（58） | 3 | 有 2 条：2027-04-01、2028-12-23 |
| `330AC0000000179` | 昭和三十年法律第百七十九号 | 補助金等に係る予算の執行の適正化に関する法律 | `330AC0000000179_20250601_504AC0000000068` | 6/0 | 37 | 有 4（例 第二十一条の二） | 9（8） | 0 | 无 |
| `331AC0000000120` | 昭和三十一年法律第百二十号 | 製造委託等に係る中小受託事業者に対する代金の支払の遅延等の防止に関する法律 | `331AC0000000120_20260101_507AC0000000041` | 0/0 | 16 | 无 | 12（11） | 0 | 无 |
| `338AC0000000154` | 昭和三十八年法律第百五十四号 | 中小企業基本法 | `338AC0000000154_20260101_507AC0000000041` | 4/4 | 32 | 无 | 27（26） | 0 | 无 |
| `363CO0000000360` | 昭和六十三年政令第三百六十号 | 消費税法施行令 | `363CO0000000360_20261001_508CO0000000108` | 5/0 | 127 | 有 51（例 第二条の二） | 143（142） | 0 | 有 4 条：2026-11-01、2027-07-22、2028-01-01、2028-04-01 |
| `405AC0000000088` | 平成五年法律第八十八号 | 行政手続法 | `405AC0000000088_20260724_508AC0000000066` | 8/3 | 48 | 有 2（例 第三十六条の二） | 17（16） | 0 | 有 1 条：2028-12-23 |
| `410AC0000000025` | 平成十年法律第二十五号 | 電子計算機を使用して作成する国税関係帳簿書類の保存方法等の特例に関する法律 | `410AC0000000025_20250401_507AC0000000013` | 0/0 | 8 | 无 | 14（13） | 0 | 有 1 条：2027-01-01 |
| `411AC0000000018` | 平成十一年法律第十八号 | 中小企業等経営強化法 | `411AC0000000018_20260101_507AC0000000041` | 7/12 | 77 | 有 1（例 第六十四条の二） | 45（44） | 0 | 无 |
| `411AC0000000042` | 平成十一年法律第四十二号 | 行政機関の保有する情報の公開に関する法律 | `411AC0000000042_20260717_508AC0000000062` | 4/0 | 27 | 有 1（例 第十二条の二） | 16（15） | 0 | 有 1 条：2026-11-02 |
| `411AC0000000128` | 平成十一年法律第百二十八号 | 不正アクセス行為の禁止等に関する法律 | `411AC0000000128_20250601_504AC0000000068` | 0/0 | 14 | 无 | 5（4） | 0 | 无 |
| `412AC0000000061` | 平成十二年法律第六十一号 | 消費者契約法 | `412AC0000000061_20260624_508AC0000000046` | 5/6 | 61 | 有 8（例 第六条の二） | 23（22） | 0 | 有 1 条：2028-12-23 |
| `412AC0000000102` | 平成十二年法律第百二号 | 電子署名及び認証業務に関する法律 | `412AC0000000102_20250601_504AC0000000068` | 6/4 | 47 | 无 | 6（5） | 0 | 无 |
| `414AC0000000151` | 平成十四年法律第百五十一号 | 情報通信技術を活用した行政の推進等に関する法律 | `414AC0000000151_20260717_508AC0000000062` | 6/5 | 28 | 无 | 51（50） | 0 | 有 2 条：2026-11-02、2028-01-16 |
| `415AC0000000057` | 平成十五年法律第五十七号 | 個人情報の保護に関する法律 | `415AC0000000057_20261001_507AC0000000070` | 8/20 | 185 | 无 | 22（21） | 2 | 有 6 条：2026-11-02、2027-01-17、2027-04-01、2027-04-16、2028-07-16、2028-12-23 |
| `416AC0000000122` | 平成十六年法律第百二十二号 | 公益通報者保護法 | `416AC0000000122_20260717_508AC0000000062` | 5/0 | 22 | 无 | 10（9） | 1 | 有 2 条：2026-11-02、2026-12-01 |
| `419AC0000000053` | 平成十九年法律第五十三号 | 統計法 | `419AC0000000053_20260624_508AC0000000046` | 7/3 | 67 | 有 5（例 第三条の二） | 6（5） | 0 | 有 2 条：2028-07-16、2028-12-23 |
| `421AC0000000066` | 平成二十一年法律第六十六号 | 公文書等の管理に関する法律 | `421AC0000000066_20261001_507AC0000000070` | 6/2 | 34 | 无 | 17（16） | 2 | 有 3 条：2026-11-02、2027-04-01、2027-04-16 |
| `425AC0000000027` | 平成二十五年法律第二十七号 | 行政手続における特定の個人を識別するための番号の利用等に関する法律 | `425AC0000000027_20261001_507AC0000000087` | 10/4 | 87 | 有 30（例 第六条の二） | 76（75） | 1 | 有 6 条：2027-01-01、2027-01-17、2028-06-04、2028-07-16、2029-06-06、2030-06-19 |
| `426AC0000000068` | 平成二十六年法律第六十八号 | 行政不服審査法 | `426AC0000000068_20260624_508AC0000000046` | 6/7 | 87 | 无 | 7（6） | 3 | 有 1 条：2028-12-23 |
| `428AC1000000103` | 平成二十八年法律第百三号 | 官民データ活用推進基本法 | `428AC1000000103_20210901_503AC0000000035` | 3/0 | 19 | 无 | 3（2） | 0 | 无 |
| `503AC0000000035` | 令和三年法律第三十五号 | デジタル社会形成基本法 | `503AC0000000035_20250401_506AC0000000046` | 6/0 | 40 | 无 | 3（2） | 0 | 无 |

### 未施行修改的重点样本

- `law_revisions.415AC0000000057.json`（個人情報の保護に関する法律）：6 条 UnEnforced，施行日 2026-11-02 ～ 2028-12-23，
  其中部分条目 `amendment_enforcement_comment` 为「…政令で定める日」且 `amendment_scheduled_enforcement_date` 有值（系统拟制日期，不是确定施行日）。
- `law_revisions.363CO0000000360.json`（消費税法施行令）：4 条 UnEnforced，最近一条 2026-11-01（令和七年政令第百二十五号）。
- `law_revisions.411AC0000000042.json`（情報公開法）、`law_revisions.416AC0000000122.json`（公益通報者保護法）：施行日 2026-11-02 / 2026-12-01，离录制日最近。
- `law_revisions.425AC0000000027.json`（番号法）：6 条 UnEnforced，最远 2030-06-19。

## 已知异常 / 与文档不一致之处（测试应覆盖）

1. **空页的 `total_count` 为 0**：`offset` 超出末尾时返回 `{"total_count":0,"count":0,"laws":[]}`，而前几页 `total_count=9567`。分页终止不能依赖 `total_count`。
2. **末页没有 `next_offset` 键**：规格写“取到末尾时为 null”，实际末页和空页都直接**省略**该键。
3. **最新已施行版本被标为 `PreviousEnforced`，履历里没有 `CurrentEnforced`**：`公文書等の管理に関する法律`（421AC0000000066）和
   `番号法`（425AC0000000027）的修订历史中没有任何 `CurrentEnforced` 条目，`asof=2026-10-20` 取回的版本（2026-10-01 施行）其
   `current_revision_status` 为 `PreviousEnforced`。采集器判断“现行版”应按施行日与 asof 比较，不能只看状态字段。
4. 两种 404 错误码不同：`law_revisions` 为 `404001`（取得結果が０件です。），`law_data` 为 `404004`（…法令本文ファイルは存在しません。）。响应体都是 `{"code","message"}`。
5. 议员立法的法令 ID 是 `AC1` 形式（如官民データ活用推進基本法 `428AC1000000103`），按 `AC0` 拼会 404。
6. 规格 2.1.139 的 `law_data` 新增 `json_format`（full/light）参数，旧版参考文档（基于 2.1.138）未列出、并建议不要使用；本样本未使用该参数。
