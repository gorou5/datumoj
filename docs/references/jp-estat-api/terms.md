# e-Stat API（jp-estat-api）利用规约要点与开放事项

S1-S8 的标签释义见 notes.md 开头；S8 只表示维护者计划及 PR 摘录，非官方来源。本文按持有页面记录，不作法律意见或许可认定。

## 1. Credit 与出处记载（F2）

- API Credit 页给出的服务文字分为两段记录如下，合并阅读时与页面文字一致：「このサービスは、政府統計総合窓口(e-Stat)のAPI機能を使用していますが、」「サービスの内容は国によって保証されたものではありません。」（api_credit.txt:5；S2）
- Credit 页的请求措辞是「クレジット表示をお願いします。」；并说可放在使用者能够参照的位置、不指定地点。（api_credit.txt:3; api_credit.txt:7；S2）
- API 规约第 7 条的措辞是「本機能を利用している出所等を明示するものとします。」并称方法另行规定。（api_terms_of_use.txt:29; api_terms_of_use.txt:30；S1）
- 站点规约 1)ア 要求注明出处，并给出 e-Stat 门户示例：「出典：政府統計の総合窓口(e-Stat)（https://www.e-stat.go.jp/）」。（site_terms_of_use.txt:10; site_terms_of_use.txt:12; site_terms_of_use.txt:13; site_terms_of_use.txt:14；S3）
- 对编辑或加工内容，站点规约要求在出处以外另行记载加工事实；并禁止把加工信息以仿佛由国家或府省制作的样态公布或利用：「編集・加工等を行ったことを記載してください。」「あたかも国（又は府省等）が作成したかのような態様で公表・利用してはいけません。」（site_terms_of_use.txt:16；S3）
- 服务 Credit 的请求措辞与 API 规约的出处措辞分别如上记录；本资料不判定两者在具体情境中的法律效力或先后关系。（api_credit.txt:3; api_credit.txt:5；api_terms_of_use.txt:30；S1、S2）

## 2. 利用条件、禁止事项与服务条款（F1、F3、F5、F6）

- API 规约要求用户注册，注册完成后发放应用程序 ID，且不得向第三方转让或出借。（api_terms_of_use.txt:8; api_terms_of_use.txt:9; api_terms_of_use.txt:10; api_terms_of_use.txt:11; api_terms_of_use.txt:12; api_terms_of_use.txt:13; api_terms_of_use.txt:14; api_terms_of_use.txt:15；S1）本地 URL 的使用方式见指南，最多 3 个 ID 见 FAQ。（api_guide.txt:15; api_guide.txt:16；api_faq.txt:58; api_faq.txt:59；S4、S7）
- API 规约第 1 条第 2 项要求使用者除本规约外，还遵守 e-Stat 上张贴的「利用の手引き、ヘルプ等」，与本规约合称「本規約等」。（api_terms_of_use.txt:6；S1）
- API 规约第 6 条说内容利用条件「政府統計の総合窓口（e-Stat）利用規約」に準じるものとします。（api_terms_of_use.txt:26; api_terms_of_use.txt:27；S1）FAQ 也按该站点利用规约说明并给出商业利用答复：「商用利用が可能です。」（api_faq.txt:41; api_faq.txt:43; api_faq.txt:44; api_faq.txt:45；S7）
- 站点规约开头记载：「複製、公衆送信、翻訳・変形等の翻案等、自由に利用できます。」「商用利用も可能です。」其前提措辞为依照 1)–6)；具体内容仍须逐项对照其页面文字。（site_terms_of_use.txt:4；S3）
- 站点规约说数值数据、简单表格和图表不属于著作权对象，并记载这些内容不适用该利用规则：「数値データ、簡単な表・グラフ等は著作権の対象ではありませんので」「本利用ルールの適用はなく」。（site_terms_of_use.txt:5；S3）此处不把概括文字套用到任何特定表，也不推定特定表无第三方权利。
- 站点规约 6)ウ 写明「CC BYに従うことでも利用することができます。」；该句针对该站点利用规约适用的内容。（site_terms_of_use.txt:60；S3）
- 站点规约说内容可能包含第三方权利，且使用者需确认；外部数据库 API 联动内容遵循提供方利用条件。（site_terms_of_use.txt:24; site_terms_of_use.txt:25; site_terms_of_use.txt:26; site_terms_of_use.txt:27; site_terms_of_use.txt:28；S3）
- API 规约禁止妨碍运行及短时间大量访问，并允许按负载限制访问；FAQ 对请求次数答称「現在のところ、ございません。」。两项表述并列记录；持有页面未列数值型每秒限制。（api_terms_of_use.txt:24; api_terms_of_use.txt:32; api_terms_of_use.txt:33; api_terms_of_use.txt:34; api_terms_of_use.txt:35; api_terms_of_use.txt:36；api_faq.txt:47; api_faq.txt:48; api_faq.txt:49；S1、S7）
- FAQ 说单次最多返回 10 万条；连续取得用 NEXT_KEY 响应值设置 startPosition。（api_faq.txt:120; api_faq.txt:121; api_faq.txt:122；S7）
- API 规约还写有无预告停止、性能劣化或规格变更，及使用者违反本规约等时无事先通告解除其使用的可能；它列出的免责文字涉及完整性、准确性、有用性、安全性、停用或中断损害、用户数据消失及应用等。（api_terms_of_use.txt:21; api_terms_of_use.txt:22; api_terms_of_use.txt:23; api_terms_of_use.txt:38; api_terms_of_use.txt:39; api_terms_of_use.txt:40; api_terms_of_use.txt:41; api_terms_of_use.txt:42; api_terms_of_use.txt:43; api_terms_of_use.txt:44；S1）
- 站点规约 5)称国家对使用者利用内容所进行的一切行为不承担责任，并说内容可能在没有预告时变更、迁移或删除。（site_terms_of_use.txt:50；site_terms_of_use.txt:52；S3）
- 站点规约 4)写明按日本法解释，并指定公开相关内容或规则的组织所在地地方法院为第一审专属合意管辖法院。（site_terms_of_use.txt:44；site_terms_of_use.txt:46；S3）
- 关于补偿，规约写明使用者以自身责任和负担解决与第三方的投诉、请求或争议，且不应给国家及统计中心带来困扰。（api_terms_of_use.txt:46; api_terms_of_use.txt:47；S1）
- 规约变更在修订文字张贴于 e-Stat 时生效；日本法为准据法，并列明东京简易法院或东京地方法院作为第一审专属合意管辖法院；手续和咨询以日语进行。（api_terms_of_use.txt:52; api_terms_of_use.txt:53; api_terms_of_use.txt:54; api_terms_of_use.txt:55; api_terms_of_use.txt:56; api_terms_of_use.txt:57; api_terms_of_use.txt:58; api_terms_of_use.txt:59；S1）
- 个人信息按另行规定的隐私政策处理；该政策文本未在持有材料内。（api_terms_of_use.txt:49; api_terms_of_use.txt:50；S1）
- 统计中心和国家可能询问 API 使用情况，并可能把所提供信息作为使用案例公开。（api_terms_of_use.txt:17; api_terms_of_use.txt:18; api_terms_of_use.txt:19；S1）
- API 规约第 4 条第 1 项另写明，被询问时使用者在可开示的范围内提供信息：「開示可能な範囲で情報を提供するものとします。」（api_terms_of_use.txt:18；S1）

## 3. API 功能的另行规则范围（F4）

- 站点规约 3)イ 将明示适用另行规则的内容列为本利用规则适用外；附件列出「ＡＰＩ機能の利用について」。（site_terms_of_use.txt:32; site_terms_of_use.txt:33; site_terms_of_use.txt:34; site_terms_of_use.txt:35; site_terms_of_use.txt:36; site_terms_of_use.txt:37; site_terms_of_use.txt:38; site_terms_of_use.txt:39; site_terms_of_use.txt:40; site_terms_of_use.txt:62; site_terms_of_use.txt:63; site_terms_of_use.txt:64; site_terms_of_use.txt:65; site_terms_of_use.txt:66; site_terms_of_use.txt:67; site_terms_of_use.txt:68; site_terms_of_use.txt:69; site_terms_of_use.txt:70；S3）
- 站点规约 3)ア 另将组织或特定事业的标志、徽标、角色设计列为本利用规则适用外：「シンボルマーク、ロゴ、キャラクターデザイン」。（site_terms_of_use.txt:34; site_terms_of_use.txt:36；S3）
- API 功能利用规约第 6 条则说 API 内容的利用条件「準じるものとします。」依照 e-Stat 站点利用规约。（api_terms_of_use.txt:26; api_terms_of_use.txt:27；S1）
- 持有页面没有进一步说明上述两处如何衔接。记录为运营方待确认项，不据此称为矛盾或冲突。（site_terms_of_use.txt:38; site_terms_of_use.txt:39; site_terms_of_use.txt:40; site_terms_of_use.txt:64; site_terms_of_use.txt:65; site_terms_of_use.txt:66; site_terms_of_use.txt:67; site_terms_of_use.txt:68; site_terms_of_use.txt:69; site_terms_of_use.txt:70；api_terms_of_use.txt:26; api_terms_of_use.txt:27；S1、S3）

## 4. 开放事项（NOT STATED）

- 每秒请求数值限制：NOT STATED；FAQ 说当前没有请求次数限制，API 规约仍写有按负载限制访问及禁止短时间大量访问。（api_faq.txt:47; api_faq.txt:48; api_faq.txt:49；api_terms_of_use.txt:24; api_terms_of_use.txt:35；S1、S7）
- 具体可用统计表、任何单表更新日程、单表许可或第三方权利标记：NOT STATED。提供数据页指向列表，但本包没有该列表内容。（api_data.txt:3; api_data.txt:4; api_data.txt:5; api_data.txt:6；api_faq.txt:76; api_faq.txt:77; api_faq.txt:78; api_faq.txt:79; api_faq.txt:80; api_faq.txt:81; api_faq.txt:82; api_faq.txt:83；S5、S7）
- 数据日期（观测值或响应的数据日）：NOT STATED；FAQ 说明不同制作机构的统计数据更新时期各异，并提到 e-Stat 按日期、府省提供新着和更新信息，但该信息仅限 Excel、CSV、PDF 格式，非 API 可用的统计数据（DB 格式）；另说明可指定更新日期参数 updatedDate 按年、月、日或范围查询近期更新的统计数据。这些文字未说明观测值或响应数据日期的规则或字段。（api_faq.txt:79-83; api_faq.txt:104-113；S7）
- API specification 页面正文、响应 schema、endpoint URLs、应用程序 ID 发放屏幕和测试表单：NOT STATED in held pages；当前材料只含对应入口或 FAQ 提及。（api_top.txt:4; api_top.txt:5; api_top.txt:6; api_top.txt:18；api_guide.txt:18; api_guide.txt:19; api_guide.txt:20; api_guide.txt:21；api_faq.txt:88；S4、S6、S7）
- API 功能另行规则与 API 规约第 6 条的衔接方式：开放事项，交由运营方确认。（site_terms_of_use.txt:38; site_terms_of_use.txt:39; site_terms_of_use.txt:40; site_terms_of_use.txt:70；api_terms_of_use.txt:26; api_terms_of_use.txt:27；S1、S3）
- 独立隐私政策正文未持有。（api_terms_of_use.txt:50；S1）
- 持有页面文字仅记录页面所述内容；不据此判断任何数据或响应可否再分发、缓存、商业使用或用于其他特定目的。
