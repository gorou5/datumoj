# e-Stat API（jp-estat-api）资料笔记

## 证据标签

S1-S7 是 2026-10-30 阅读的官方页面保存文字；S8 是维护者计划及 PR 摘录，不是官方来源。未实施实时 API 请求。

| 标签 | 官方页面或保存文件 | 验证级别 |
|---|---|---|
| S1 | e-Stat API 功能利用规约：<https://www.e-stat.go.jp/api/terms-of-use> | 读过官方页面（2026-10-30） |
| S2 | e-Stat API Credit 显示：<https://www.e-stat.go.jp/api/api-info/credit> | 读过官方页面（2026-10-30） |
| S3 | e-Stat 站点利用规约：<https://www.e-stat.go.jp/terms-of-use> | 读过官方页面（2026-10-30） |
| S4 | e-Stat API 利用指南：<https://www.e-stat.go.jp/api/api-info/api-guide> | 读过官方页面（2026-10-30） |
| S5 | e-Stat API 提供数据页：<https://www.e-stat.go.jp/api/api-info/api-data> | 读过官方页面（2026-10-30） |
| S6 | e-Stat API 功能首页：<https://www.e-stat.go.jp/api/> | 读过官方页面（2026-10-30） |
| S7 | e-Stat API FAQ：<https://www.e-stat.go.jp/api/api-dev/faq> | 读过官方页面（2026-10-30） |
| S8 | datumoj_plan_excerpt.md 与 datumoj_pr38_body.md（维护者文字，非官方来源） | 仅读文档 |

## 1. 访问与应用程序 ID（F1）

- API 仅限完成用户注册者使用；指南也将用户注册列为第一步。（api_terms_of_use.txt:8; api_terms_of_use.txt:9；api_guide.txt:6; api_guide.txt:7; api_guide.txt:8；S1、S4）
- 用户注册完成后，统计中心发放应用程序 ID；指南说每个开发应用程序取得一个 ID，且请求时需要发送该 ID。（api_terms_of_use.txt:11; api_terms_of_use.txt:12; api_terms_of_use.txt:13；api_guide.txt:12; api_guide.txt:13; api_guide.txt:14; api_guide.txt:15; api_guide.txt:16；api_faq.txt:54; api_faq.txt:55; api_faq.txt:56; api_faq.txt:57; api_faq.txt:58; api_faq.txt:59；S1、S4、S7）
- FAQ 说可取得的应用程序 ID 最多 3 个；ID 不得转让或出借给第三方。（api_faq.txt:58; api_faq.txt:59；api_terms_of_use.txt:13；S1、S7）
- 若应用程序不在公开网站使用，指南允许在发放时填写本地地址；未读取发放画面。（api_guide.txt:15; api_guide.txt:16；S4）
- 发现 ID 被第三方不正当使用时，使用者须尽快联系统计中心；统计中心在收到使用者关于发现该 ID 被第三方不正当使用的报告时，或该中心判断该 ID 被第三方不正当使用或疑似被第三方不正当使用时，可立即停止该 ID，并可中断 API 功能的使用。（api_terms_of_use.txt:14; api_terms_of_use.txt:15；S1）
- FAQ 说更改应用程序 ID 须先删除、再重新发放（「削除後に、再発行することで可能です」），更改前的 ID 将无法再使用。（api_faq.txt:69; api_faq.txt:70; api_faq.txt:71；S7）
- API 首页和 FAQ 指向的测试表单以及规格页仅有链接或介绍；其页面内容未纳入本包。（api_top.txt:4; api_top.txt:5; api_top.txt:6; api_top.txt:18；api_faq.txt:88；S6、S7）

## 2. Credit、内容利用与规则范围（F2-F4）

- API 服务 Credit 页请求公开使用 API 的服务显示指定文字，并说只要使用者可参照即可、不限定显示位置；API 规约第 7 条另要求按另行指定的方法标明使用 API 的出处等。（api_credit.txt:3; api_credit.txt:5; api_credit.txt:7；api_terms_of_use.txt:29; api_terms_of_use.txt:30；S1、S2）详见 terms.md 的分项记录。
- API 规约第 6 条把 API 内容利用条件指向 e-Stat 站点利用规约；FAQ 也重复这一指向并写有商业利用答复。（api_terms_of_use.txt:26; api_terms_of_use.txt:27；api_faq.txt:41; api_faq.txt:45；S1、S7）
- 站点规约开头记录了复制、公众传输、翻译或改编及商业利用的文字；另称数值数据、简单表格和图表不属著作权对象，并称相关规则不适用。（site_terms_of_use.txt:4; site_terms_of_use.txt:5；S3）这里不将该表述扩展到任何特定表，也不据此判断其第三方权利。
- 站点规约另说内容可能有第三方权利，未明确处理者由使用者确认；外部数据库 API 联动取得的内容遵循提供方利用条件。（site_terms_of_use.txt:24; site_terms_of_use.txt:25; site_terms_of_use.txt:26; site_terms_of_use.txt:27; site_terms_of_use.txt:28；S3）
- 站点规约第 6)ウ 写明与 CC BY 4.0 兼容；该句指向适用站点规则的内容。（site_terms_of_use.txt:60；S3）
- 范围待确认：站点规约 3)イ 和附件把「ＡＰＩ機能の利用について」列为另行规则项目；API 规约第 6 条又把内容利用条件指向站点规约。持有页面没有进一步说明两处如何衔接；若维护者需要确定，应由运营方确认。（site_terms_of_use.txt:38; site_terms_of_use.txt:39; site_terms_of_use.txt:40; site_terms_of_use.txt:70；api_terms_of_use.txt:26; api_terms_of_use.txt:27；S1、S3）

## 3. 限制与行为（F5）

- FAQ 对 API 请求使用次数的回答为目前没有限制；API 规约同时允许按负载限制访问，并禁止短时间大量访问或妨碍运行。（api_faq.txt:47; api_faq.txt:48; api_faq.txt:49；api_terms_of_use.txt:21; api_terms_of_use.txt:22; api_terms_of_use.txt:23; api_terms_of_use.txt:24; api_terms_of_use.txt:32; api_terms_of_use.txt:33; api_terms_of_use.txt:34; api_terms_of_use.txt:35; api_terms_of_use.txt:36；S1、S7）
- API 规约允许在违反禁止事项或判断存在风险时停止使用。持有页面没有写明每秒请求数值。（api_terms_of_use.txt:34; api_terms_of_use.txt:35; api_terms_of_use.txt:36；api_faq.txt:49；S1、S7）
- FAQ 写明单次最多返回 10 万条；继续取得时，响应给出 NEXT_KEY 值，使用者把该值放入 startPosition 参数。（api_faq.txt:120; api_faq.txt:121; api_faq.txt:122；S7）
- 维护者计划本身将官方表述与项目限速分开记录，并在其速率表列出 rate_per_sec 项目值；这是维护者项目配置，不是官方页面公布的限额。（datumoj_plan_excerpt.md:50; datumoj_plan_excerpt.md:51; datumoj_plan_excerpt.md:52; datumoj_plan_excerpt.md:53; datumoj_plan_excerpt.md:54; datumoj_plan_excerpt.md:55；S8）

## 4. 数据范围与日期（F7-F8）

- API 提供数据页称页面显示的统计列表即当前 API 提供的数据；FAQ 将可取得范围说明为 e-Stat 数据库中显示的数据。（api_data.txt:3; api_data.txt:4; api_data.txt:5; api_data.txt:6；api_faq.txt:73; api_faq.txt:74; api_faq.txt:75; api_faq.txt:76; api_faq.txt:77；S5、S7）
- 小地域和地域网格数据不显示在该列表；取得时需指定 searchKind=2，提供数据页说版本 3.0 包含最新信息。（api_data.txt:5; api_data.txt:6；api_faq.txt:98; api_faq.txt:99; api_faq.txt:100; api_faq.txt:101; api_faq.txt:102；S5、S7）
- FAQ 说省略 searchKind 时选 1（统计情报）；版本 2.1 以前虽可指定 searchKind=3（社会・人口统计体系），但不指定也能取得该数据且可取得最新数据，因此 FAQ 请使用者不要指定 3。（api_faq.txt:101; api_faq.txt:102；S7）
- 更新时期因制作机构而异；FAQ 指向数据制作机构查询。它也列出 updatedDate 的年、月、日和范围形式。（api_faq.txt:79; api_faq.txt:80; api_faq.txt:81; api_faq.txt:82; api_faq.txt:83; api_faq.txt:104; api_faq.txt:105; api_faq.txt:106; api_faq.txt:107; api_faq.txt:108; api_faq.txt:109; api_faq.txt:110; api_faq.txt:111; api_faq.txt:112; api_faq.txt:113；S7）
- 统计表 ID 对应响应中的 LIST_INF 标签 id 属性，约 10 位；e-Stat 的按日新到信息仅包括 Excel、CSV、PDF，不包括数据库形式的 API 数据。（api_faq.txt:90; api_faq.txt:91; api_faq.txt:92; api_faq.txt:79; api_faq.txt:80; api_faq.txt:81; api_faq.txt:82; api_faq.txt:83；S7）
- 日期：站点规约称其定于 2016-01-29 制定，并遵循政府标准利用规约 2.0 版；S1、S2、S4、S5、S7 所持文字未载明制定日期（NOT STATED）。（site_terms_of_use.txt:58；api_terms_of_use.txt:1; api_terms_of_use.txt:59；api_credit.txt:1; api_credit.txt:2; api_credit.txt:3; api_credit.txt:4; api_credit.txt:5; api_credit.txt:6; api_credit.txt:7；api_guide.txt:1; api_guide.txt:28；api_data.txt:1; api_data.txt:2; api_data.txt:3; api_data.txt:4; api_data.txt:5; api_data.txt:6；api_faq.txt:1; api_faq.txt:122；S1-S7）
- 站点规约 6)イ 还写明该规则今后可能变更，并称已按政府标准利用规约以前版本利用内容的，「引き続きその条件が適用されます」。（site_terms_of_use.txt:58；S3）
- API 首页最新列出的新闻日期为 2026-07-10，标题为「消費者物価指数の基準改定に伴うＡＰＩ機能で利用可能な統計表ID等の変更について」；这里只记录页面所列日期与标题。（api_top.txt:22; api_top.txt:23; api_top.txt:24；S6）
- 本次页面文字核对日期为 2026-10-30；这是本次读取日期，不是规约或页面文件的制定日期。（S1-S7；仅读文档）

