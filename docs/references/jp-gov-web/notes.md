# 政策网页参考包（jp-gov-web）资料笔记

## 证据标签

下表中的 URL 是捕获头部记录的来源地址；引用只指向正文行，不引用网页文件的第 1 行。S11 是维护者配置摘录，不是官方来源。R1 是本方自有 robots 捕获观察，不是站点条款；记录日志不随参考包分发。

| 标签 | 页面或材料 | 用途 |
|---|---|---|
| S1 | 内閣府イノベーション推進种子页；cao_seed.txt；https://www8.cao.go.jp/cstp/openinnovation/index.html | 种子页正文（cao_seed.txt:2） |
| S2 | 内閣府ホームページ利用規約；cao_rule.txt；https://www.cao.go.jp/notice/rule.html | 内閣府自己的站点规则（cao_rule.txt:2） |
| S3 | 総務省地域におけるデジタル・トランスフォーメーション种子页；soumu_seed.txt；https://www.soumu.go.jp/denshijiti/digital_transformation.html | 种子页正文（soumu_seed.txt:2） |
| S4 | 総務省当省ホームページについて；soumu_tyosaku.txt；https://www.soumu.go.jp/menu_kyotsuu/policy/tyosaku.html | 総務省自己的著作权与站点说明（soumu_tyosaku.txt:2） |
| S5 | 大阪市经济战略局分类页；osaka_seed.txt；https://www.city.osaka.lg.jp/keizaisenryaku/category/3037-1-0-0-0-0-0-0-0-0.html | 种子页正文（osaka_seed.txt:2） |
| S6 | 大阪市サイトの考え方；osaka_site_policy.txt；https://www.city.osaka.lg.jp/main/site_policy/0000000120.html | 页面日期及条款导航（osaka_site_policy.txt:50） |
| S7 | 大阪市掲載データを使う；osaka_use_data.txt；https://www.city.osaka.lg.jp/main/site_policy/0000000160.html | 数据利用概要及文件标记说明（osaka_use_data.txt:51） |
| S8 | 大阪市著作権・免責；osaka_copyright.txt；https://www.city.osaka.lg.jp/main/site_policy/0000000124.html | 大阪市自己的著作权规则（osaka_copyright.txt:51） |
| S9 | デジタル庁公共データ利用規約（第1.0版）；pdl_v1.txt；https://www.digital.go.jp/resources/open_data/public_data_license_v1.0 | 单独的标准文本；不替代 S2 或 S4 的原句（pdl_v1.txt:50） |
| S10 | 総務省 robots.txt；soumu_robots.txt | 已捕获的正文行（soumu_robots.txt:2-3） |
| S11 | 上游维护者的 jp-gov-web YAML 摘录；datumoj_plan_excerpt.md | 仅作配置主张的比对对象，不是官方页面证据（datumoj_plan_excerpt.md:2-40） |
| R1 | 本方自有 robots.txt 捕获观察（日志不随参考包分发） | 2026-10-30 的三个 URL 与状态见本节下文 |

获取方式：捕获记录头将各文件标作页面文本捕获，并记录来源 URL、HTTP 状态与捕获时间；记录头未记载请求方法。上述信息仅作捕获头元数据，不作为页面正文引用。

## 1. 三个种子页面

- S1 的页面标题为「イノベーション推進 - 科学技術・イノベーション - 内閣府」（S1；cao_seed.txt:2）；正文目录还列出「SBIR制度」（S1；cao_seed.txt:8）。
- S3 的页面标题为「総務省｜地域におけるデジタル・トランスフォーメーション」（S3；soumu_seed.txt:2）；正文说明该页围绕自治体 DX 与地域社会 DX（S3；soumu_seed.txt:72-79）。
- S5 的页面标题为「大阪市経済戦略局：産業支援・創出支援・特区制度など （各種支援・相談・案内・お知らせ）」（S5；osaka_seed.txt:2）；正文显示分类名与页面编号（S5；osaka_seed.txt:52-55）。
- 配置摘录中的 URL、路径前缀和种子标签属于维护者配置主张；逐项状态仅见维护者专用对照表 SOURCE_CHECK_TABLE.md（不随公开参考包分发）。公开参考包不提供逐项配置、运行或链接跟随核验；页面正文没有被当作配置运行或链接跟随行为的证据。

## 2. 站点页面自己的规则文字

- 内閣府页面的表述是：「著作権は、特記されていない限り内閣府に帰属」（S2；cao_rule.txt:9），并写有「権利表記の記載がない限り」（S2；cao_rule.txt:9）公共数据利用规约第 1.0 版的条件句（S2；cao_rule.txt:9）。
- 総務省页面另用自己的措辞：「著作権は、特記されていない限り総務省に帰属」（S4；soumu_tyosaku.txt:127），并写有「権利表記の記載がない限り」（S4；soumu_tyosaku.txt:127）及「公共データ利用規約（第1.0版）」（S4；soumu_tyosaku.txt:127）。这两条记录保留各自页面原句。
- 大阪市「サイトの考え方」捕获文本列出页面日期与著作权导航（S6；osaka_site_policy.txt:50-56）。其概要页称自己是著作权页的概要，并要求遇到页面上标示的其他许可或利用规则时依该标示处理（S7；osaka_use_data.txt:53,57-58）。
- 大阪市概要页区分有标记和无标记的附件：有许可标记的文件按其标记记录；没有标记时，页面要求使用者自行确认（S7；osaka_use_data.txt:68-80）。完整著作权页另有附件规则（S8；osaka_copyright.txt:92-99）。

## 3. 例外、第三方权利与其他标记

- S9 的 PDL1.0 文本说明可能存在第三方权利，且部分权利区分可能没有被明确标出（S9；pdl_v1.txt:65-70）。该标准还要求除明确标示完成权利处理的内容外由使用者取得第三方许可（S9；pdl_v1.txt:66），并要求通过 API 等从外部数据库取得的内容遵从提供方条件（S9；pdl_v1.txt:69）。内閣府与総務省各自引用 S9；此处不把它扩展成大阪市条款。
- S10 的正文记录「User-agent: ia_archiver」（S10；soumu_robots.txt:2）及「Disallow: /」（S10；soumu_robots.txt:3）。R1 是本方于 2026-10-30 的 robots.txt 捕获观察，记录日志本身不随参考包分发：https://www8.cao.go.jp/robots.txt 返回 404，https://www.soumu.go.jp/robots.txt 返回 200，https://www.city.osaka.lg.jp/robots.txt 返回 404；総務省的 200 记录仅限首 600 字节内所见的唯一规则。404 不表示许可，任何 robots 观察均不作许可或禁止结论。
- 抓取配置的 rate、PDF 跟随、种子范围与保留策略均是配置项。不得从站点条款、robots 结果或页面标题推出这些参数获准或实际运行。

## 4. 日期与范围限制

- 本包只按各已捕获页面正文逐条记录，不把捕获时间当作页面更新日。

来源级时间（captured_at 来自捕获记录头；记录头时间仅作元数据，不作为页面正文引用）：

| 标签 | 捕获文件 | captured_at | published_at |
|---|---|---|---|
| S1 | cao_seed.txt | 2026-10-30 16:32 JST | unknown |
| S2 | cao_rule.txt | 2026-10-30 16:32 JST | unknown |
| S3 | soumu_seed.txt | 2026-10-30 16:32 JST | unknown |
| S4 | soumu_tyosaku.txt | 2026-10-30 16:32 JST | unknown |
| S5 | osaka_seed.txt | 2026-10-30 16:32 JST | unknown |
| S6 | osaka_site_policy.txt | 2026-10-30 16:32 JST | unknown |
| S7 | osaka_use_data.txt | 2026-10-30 16:32 JST | unknown |
| S8 | osaka_copyright.txt | 2026-10-30 16:33 JST | unknown |
| S9 | pdl_v1.txt | 2026-10-30 16:33 JST | unknown |
| S10 | soumu_robots.txt | 2026-10-30 16:32 JST | unknown |

S6 与 S7 页面文本分别显示日期 2026年6月26日、2026年3月9日，但未标明为更新日或发布日期，故 published_at 记为 unknown（osaka_site_policy.txt:52；osaka_use_data.txt:52）。
- 本包没有逐一检查被抓取的单页及附件（包括 PDF）的权利。站点级条款写明各自的适用规则与例外；具体目标页面或 PDF 是否带有其他权利标记为 NOT STATED（S2；cao_rule.txt:9,13-14；S4；soumu_tyosaku.txt:127,141-146；S7；osaka_use_data.txt:58,68-80；S8；osaka_copyright.txt:94-99）。
- 本包不判断某一页面或附件适用何种许可，不作再分发结论，不表示此次或任何抓取获准。未读材料及配置对照仅在维护者专用 SOURCE_CHECK_TABLE.md 汇总（不随公开参考包分发）；公开参考包不提供逐项配置或未读材料核验。

## 5. 上游配置摘录

- S11 是维护者自有配置摘录。其 seed、host、rate、PDF-following、retention 与 licence 字段仅作为配置主张核对，不作为站点事实（S11；datumoj_plan_excerpt.md:2-40）。
