# 東京都 青ヶ島村 町字マスター（jp-abr）资料笔记

## 证据标签

| 标签 | 官方页面或保存结果 | 验证级别 |
|---|---|---|
| S1 | デジタル庁 アドレス・ベース・レジストリの利用規約：<https://www.digital.go.jp/policies/base_registry_address_tos>。（abr_terms.txt） | 读过官方页面（2026-10-30） |
| S2 | デジタル庁 アドレス・ベース・レジストリ：<https://www.digital.go.jp/policies/base_registry_address>。（abr_overview.txt） | 读过官方页面（2026-10-30） |
| S3 | ArcGIS Hub 的東京都 青ヶ島村 町字マスター条目页：<https://dataset.address-br.digital.go.jp/documents/fbd6a731462c4fc7a31af06a5b98192d/about>；条目值按保存页面呈现记录，界面文案由采集浏览器以简体中文显示。（abr_dataset_item.txt） | 读过官方页面（2026-10-30） |
| S4 | Datumoj 维护者计划摘录；不是官方来源，只用于核对计划文字。（datumoj_plan_excerpt.md） | 仅读文档 |

本文件中的 `文件名:行号` 指向未随本包提交的本地保存记录（公开仓库不提交未核对再分发范围的原文）；读者核对时以各标签对应的官方 URL 为准。

## 1. 数据集身份与范围

- S3 条目标题值为 東京都 青ヶ島村 町字マスター，发布者值为 デジタル庁 ベース・レジストリ。（S3；abr_dataset_item.txt:5; abr_dataset_item.txt:6）
- S3 页面显示市区町村级与東京都；这是保存页面显示的条目值。（S3；abr_dataset_item.txt:11; abr_dataset_item.txt:12）
- S2 将町字文字信息列为已完成整备，并记载其正式版公开日期。「2025年6月1日に町字データを正式版として公開しました」（S2；abr_overview.txt:18）
- S2 说明町字地图信息、地番及住居表示文字/地图信息仍在整备方针讨论中；现行公开数据属试验公开版。「現在公開しているデータについては、試験公開版である点に留意してご利用をお願いします。」（S2；abr_overview.txt:50）
- S2 说明町字数据以 CSV 形式公开，并提供 ABR geocoder；试验公开版采用的行政数据来源列在同页。（S2；abr_overview.txt:38; abr_overview.txt:40; abr_overview.txt:42; abr_overview.txt:61）
- S2 列出的试验版来源数据：「電子国土基本図（地名情報）（国土地理院）、位置参照情報（国土交通省）、登記所備付地図データ（法務省）」（S2；abr_overview.txt:40）

## 2. 条款与页面注意事项

- S1 的页面更新日期值为「2026年5月29日」（S1；abr_terms.txt:6）。
- S2 的页面更新日期值为「2026年3月30日」（S2；abr_overview.txt:6）。
- S1 将 PDL1.0 表述为在未记载权利表记时适用；完整条款范围与条目标记之间的关系没有在本包中裁定。「権利表記の記載がない限り」（S1；abr_terms.txt:8）
- S1 的 1.3 列出地番マスター与地番マスター位置参照，并说明使用时连同本规约视为同意登記所備付地図データ利用規約；见 terms.md。（S1；abr_terms.txt:21; abr_terms.txt:22; abr_terms.txt:23; abr_terms.txt:24）
- S1 的 1.4 说明标志、Logo、角色设计，以及明确注明适用其他利用规则的内容不适用该利用规则；见 terms.md。（S1；abr_terms.txt:27; abr_terms.txt:29; abr_terms.txt:30; abr_terms.txt:31）
- **正式版更正事项。** S2 说明，自治体确认后的正式版町字数据相关留意事项已处理，已开始公开修正后的数据，并请查看下载站通知；页面另列留意对象自治体、记录及更正记录清单，本包未检查这些清单或下载站。（S2；abr_overview.txt:67；abr_overview.txt:82；abr_overview.txt:83；abr_overview.txt:84；abr_overview.txt:85；abr_overview.txt:86；abr_overview.txt:87）

## 3. 条目值、日期与新鲜度

- S3 摘要区域显示最終更新日值「2026-03-10T12:05:43.000Z」（S3；abr_dataset_item.txt:10）。
- S3 以浏览器界面文案显示两项时间值：2025年8月9日 GMT+9 18:27:53 与 2025年8月9日 GMT+9 18:27:53；相邻界面标签分别呈现为更新日期与发布日期。标签是采集浏览器渲染文字，不是发布者措辞。（S3；abr_dataset_item.txt:17; abr_dataset_item.txt:18; abr_dataset_item.txt:19; abr_dataset_item.txt:20）
- S3 显示大小值「77 B」，相邻界面标签呈现为文档文件大小；这是条目页的显示值，本包不把它解释为 CSV ZIP 文件大小。（S3；abr_dataset_item.txt:21; abr_dataset_item.txt:22）
- S3 显示 CC BY 4.0 许可名称值；只引用该值，不把界面标签当作许可正文。（S3；abr_dataset_item.txt:25）
- 2026-10-30 是本次采集/核对日期，不是上述页面更新日期或条目日期。S1-S3 未列固定更新周期；S2 说明自 2025 年 6 月起随时收集自治体更新信息并提供，但未给出固定周期。也没有对文件执行 HEAD 或下载来核验 Last-Modified、文件大小或归档内容。（S2；abr_overview.txt:42；仅读文档；S1-S3）
- 数据集文件下载主机、文件 URL、官方代码 134023、旧 S3 主机条目、robots.txt 结果均 NOT STATED 于本次可读的页面正文；对应未捕获材料和缺口见 terms.md。（仅读文档；S1-S4）

## 4. 与 DATUMOJ 计划的对照

- S4 把文件名、下载地址、561 字节和 Last-Modified 一致性列作计划事实；S1-S3 已读正文没有陈述这些文件级事项。（S4；datumoj_plan_excerpt.md:16; datumoj_plan_excerpt.md:17；S1-S3）
- S4 还列出旧主机与 robots.txt 返回值；本次没有捕获旧条目或 robots.txt，均保留为 NOT STATED。（S4；datumoj_plan_excerpt.md:24; datumoj_plan_excerpt.md:56；S1-S3）
- 本资料不核验地区/表格选择、样本预算、请求率或主机白名单等计划配置；逐项证据状态见独立对照表。（S4；datumoj_plan_excerpt.md:15; datumoj_plan_excerpt.md:22; datumoj_plan_excerpt.md:48; datumoj_plan_excerpt.md:52）
