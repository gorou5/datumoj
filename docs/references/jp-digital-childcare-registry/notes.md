# 子育て支援制度レジストリ（jp-digital-childcare-registry）资料笔记

## 证据标签

S1–S4 是所持官方 PDF 文本或官方页面文本；S5 是维护者自己的提案文本，不是官方来源。未进行 live API 请求，也未取得 API 规格书。URL 取自捕获文件第 1 行的头部元数据，仅作来源入口，不作为页面正文引文。（S1–S5）

| 标签 | 官方页面或保存结果 | 验证级别 |
|---|---|---|
| S1 | マイナポータルAPI 利用規約 PDF 文本；标题页写明 2.3 版与日期。<https://files.microcms-assets.io/assets/7c793323a46a46b7bb9a2ac7d0023301/535e70eb7c5b4918b5ae4b6b202c74c4/mynaportal-api-terms.pdf>（inputs/held/myna_terms.txt:15；inputs/held/myna_terms.txt:28；inputs/held/myna_terms.txt:30） | 读过官方文档（2026-10-30） |
| S2 | 機密保持誓約書 PDF 文本；无签署日期以外的文件日期。<https://files.microcms-assets.io/assets/7c793323a46a46b7bb9a2ac7d0023301/f94b4980b0074de99b70a1480adb85d3/mynaportal-api-commitment.pdf>（inputs/held/myna_commitment.txt:9；inputs/held/myna_commitment.txt:74） | 读过官方文档（2026-10-30） |
| S3 | デジタル庁开发者站点的電子申請等API 页面。<https://developers.digital.go.jp/documents/mynaportal-api/specification/eshinsei/> | 读过官方页面（2026-10-30） |
| S4 | デジタル庁子育て支援制度レジストリ服务页。<https://services.digital.go.jp/childrearing-support-registry/> | 读过官方页面（2026-10-30） |
| S5 | DATUMOJ Issue 49 的维护者提案文本；非官方来源。<https://github.com/gorou5/datumoj/issues/49> | 仅读文档 |

## 1. 注册库 API 与返回信息

S3 在子育て支援制度レジストリ项目下列出两个 API：外部連携制度・手続一覧検索API 提供制度与手续一览，外部連携制度・手続詳細検索API 提供详细信息。所列信息来自自治体及国家登记的数据。（读过官方页面（2026-10-30）；S3）inputs/held/eshinsei_page.txt:38；inputs/held/eshinsei_page.txt:39

S4 描述该注册库为汇集、结构化国与自治体子育て支援制度信息的数据库；自治体通过マイナポータル申請管理登记制度信息，应用服务商通过 API 取得这些信息。（读过官方页面（2026-10-30）；S4）inputs/held/registry_service_page.txt:19；inputs/held/registry_service_page.txt:20

S4 在「サービスの特徴」的「情報の網羅性」项下称：「主に未就学児を対象とした124種類の制度類型をカバーしております。」这是所读页面的描述，不据此推断当前覆盖是否完整、API 记录数或任何资格规则。（读过官方页面（2026-10-30）；S4）inputs/held/registry_service_page.txt:35；inputs/held/registry_service_page.txt:36；inputs/held/registry_service_page.txt:38

本节的 API 名称与返回内容只依据 S3 的列表说明；不依据 S5 的 JSON 草案推断字段或响应结构。S3 页面不包含 API 规格书；该规格书需要另行申请。（读过官方页面（2026-10-30）；S3）inputs/held/eshinsei_page.txt:38；inputs/held/eshinsei_page.txt:39；inputs/held/eshinsei_page.txt:136；inputs/held/eshinsei_page.txt:137

## 2. 申请流程参考期间与提案数字对照

S3 对电子申请等 API 页面给出整体参考期，并逐步列出参考期间。页面说明这些数字基于过去申请者实际耗时，实际时间会随团队、API 类型与服务规模变化。（读过官方页面（2026-10-30）；S3）inputs/held/eshinsei_page.txt:71；inputs/held/eshinsei_page.txt:72；inputs/held/eshinsei_page.txt:76

| 阶段 | S3 页面印载的参考期间 | S5 提案数字 | 对照 |
|---|---|---|---|
| 从申请到服务开始（整体） | 约半年至约一年（inputs/held/eshinsei_page.txt:71） | S5 写每阶段约 5–15 个工作日（inputs/held/datumoj_issue49_body.md:27） | DIFFERS：整体期间与逐阶段数字范围不同；只记录两处文字，不判断哪处正确。 |
| 利用申请 | 约 1 周（inputs/held/eshinsei_page.txt:87） | S5 写每阶段约 5–15 个工作日（inputs/held/datumoj_issue49_body.md:27） | DIFFERS：印载数字不同；不判断哪处正确。 |
| 审查 | 约 2 周（inputs/held/eshinsei_page.txt:92） | S5 写每阶段约 5–15 个工作日（inputs/held/datumoj_issue49_body.md:27） | DIFFERS：印载数字不同；不判断哪处正确。 |
| 开发 | 约 5 个月（inputs/held/eshinsei_page.txt:97） | S5 写每阶段约 5–15 个工作日（inputs/held/datumoj_issue49_body.md:27） | DIFFERS：印载数字不同；不判断哪处正确。 |
| 接続試験 | 约 4 个月（inputs/held/eshinsei_page.txt:103） | S5 写每阶段约 5–15 个工作日（inputs/held/datumoj_issue49_body.md:27） | DIFFERS：印载数字不同；不判断哪处正确。 |
| 本番動作確認 | 约 1 个月（inputs/held/eshinsei_page.txt:111） | S5 写每阶段约 5–15 个工作日（inputs/held/datumoj_issue49_body.md:27） | DIFFERS：印载数字不同；不判断哪处正确。 |
| 服务提供前确认 | 约 1–2 周（inputs/held/eshinsei_page.txt:119） | S5 写每阶段约 5–15 个工作日（inputs/held/datumoj_issue49_body.md:27） | DIFFERS：印载数字不同；不判断哪处正确。 |

S3 页面对各阶段另列前提与期限如下；这些是 S3 对電子申請等API 一般流程的文字，仅使用注册库 API 的实现是否需要每一项，所持文本未说明。（读过官方页面（2026-10-30）；S3）inputs/held/eshinsei_page.txt:69；inputs/held/eshinsei_page.txt:71

- 接続試験：使用测试用マイナンバーカード实施，须向 J-LIS 申请借用并在接続試験开始前准备；iOS 的接続試験使用 TestFlight，须事先准备 Apple ID。（读过官方页面（2026-10-30）；S3）inputs/held/eshinsei_page.txt:105；inputs/held/eshinsei_page.txt:106
- 本番動作確認：须在开始预定日的 15 个营业日前提交申请（「15営業日前までに」）；使用符合利用目的的マイナンバーカード实施，须在开始前准备。（读过官方页面（2026-10-30）；S3）inputs/held/eshinsei_page.txt:109；inputs/held/eshinsei_page.txt:113
- 服务提供前确认：须在服务开始预定日的 2 周前提交本番動作確認完了報告書（「2週間前までに」）；数字厅受领后进行确认，确认完成并通知结果后，事业者才开始提供服务。（读过官方页面（2026-10-30）；S3）inputs/held/eshinsei_page.txt:116；inputs/held/eshinsei_page.txt:117；inputs/held/eshinsei_page.txt:118

## 3. 材料日期

| 材料 | 所持文本显示的日期 | 核对位置 |
|---|---|---|
| S1 利用规约 | 标题页 2.3 版、令和 8 年 6 月 1 日；改版履历的 2.3 行也写 R8/6/1。 | inputs/held/myna_terms.txt:28；inputs/held/myna_terms.txt:30；inputs/held/myna_terms.txt:86 |
| S2 机密保持誓约 | 未标文件日期；誓约书签署栏是空白年、月、日。 | inputs/held/myna_commitment.txt:2；inputs/held/myna_commitment.txt:74 |
| S3 電子申請等API 页面 | 公開日 2026年06月16日；更新日 2026年10月01日。 | inputs/held/eshinsei_page.txt:11；inputs/held/eshinsei_page.txt:13 |
| S4 注册库服务页 | 公告日期 2025年11月21日，公告内容为页面公开。 | inputs/held/registry_service_page.txt:5；inputs/held/registry_service_page.txt:6；inputs/held/registry_service_page.txt:7 |

S1/S2 的持有文本头部记载提取日期；S3/S4 的页面文本头部记载读取日期。这些读取/提取时间不替代材料自身显示的版本或发布日期。（inputs/held/myna_terms.txt:2；inputs/held/myna_commitment.txt:2；inputs/held/eshinsei_page.txt:2；inputs/held/registry_service_page.txt:2）

## 4. 提案来源分界

S5 的 API key、专用利用规约页面、5–15 个工作日及全量 CSV 说法均为维护者提案中的说法。所持官方材料对这些具体主张的支持程度逐项列于 字段逐项对照表（维护者侧材料，未随本 PR 提交）；未把 S5 单独陈述当作官方核实结论。（仅读文档；S5）inputs/held/datumoj_issue49_body.md:27；inputs/held/datumoj_issue49_body.md:29；inputs/held/datumoj_issue49_body.md:37；inputs/held/datumoj_issue49_body.md:73

S5 attributes the planned all-municipality CSV to an MHLW administrative notice; the notice itself is not in the held materials, so this remains a maintainer claim (inputs/held/datumoj_issue49_body.md:29).
