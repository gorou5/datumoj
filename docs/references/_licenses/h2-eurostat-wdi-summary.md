# 来源条款摘要

下表仅概括本包读取的保存页面；逐项来源、限定条件与证据边界见各包的 terms.md。保存页面未涉及 AI/模型处理，不从沉默推断许可。

| Source ID | 复用 / 再分发 | 自动化访问 | 商业使用 | 署名 | Key | 限制 | 云端模型处理 | 待确认单元 |
|---|---|---|---|---|---|---|---|---|
| intl-eurostat | 版权 notice 的一般复用表述：「Reuse of statistical data, metadata, publications, and other dissemination tools published on this website for commercial or non-commercial purposes is authorised provided the source is acknowledged.」；具体数据项和个别 notice 待核对。 | Statistics API 页面称供公众使用；未陈述自动化请求规则，详细指南未保存。 | 页面对所列例外表述：「may not be reused for commercial purposes, but non-commercial reuse is possible without restriction」；逐数据项待核对。 | 数据集 DOI + 访问日期；自定义数据集 datacode + 访问日期；其他产品标题、链接、访问日期。 | 保存页面未说明；待确认。 | 未见数字 QPS、并发或查询行数上限；SDMX 页面只描述响应规模和处理时间。 | 保存页面未涉及。 | Commission legal notice；详细 API 指南；逐数据集版权 notice；Key/认证；数值限制；云端模型处理。 |
| intl-worldbank-wdi | Dataset Terms 记载 CC BY 4.0（除非另有标示）及附加条款；原文：「You may extract, download, and make copies of the data contained in the Datasets」；特定指标待核对。 | general Terms 要求请求量合理，禁止过量或滥用式使用，并说明可监测；没有数字阈值。 | Dataset Terms 与 remainder of Materials 的 general 条款分层；原文：「For the remainder of the Materials, you may make non-commercial uses thereof」；逐指标适用范围待确认。 | The World Bank: Dataset name: Data source (if known)；再许可须保留相同署名要求。 | Indicators API 文章称无需 API key 或其他认证。 | 未见数字请求速率、大小、并发或分页上限；具体 API 参考页未保存。 | 保存页面未涉及。 | WDI 数据集和各指标元数据；第三方例外；CC BY 4.0 法律文本；Basic Call Structure；云端模型处理。 |

## Owner 与法务待确认问题

- Owner：本来源计划覆盖哪些 Eurostat 数据集、数据代码、国家范围及系列来源？
- Owner：Eurostat Key/认证状态以及请求容量应以哪些未保存的官方 API 指南核对？
- Owner：WDI 采集范围内每个指标应如何绑定目录元数据和来源说明？
- 法务：Eurostat 页面列出的商业用途例外、第三方权利和未保存的 European Commission legal notice 如何适用于拟采集数据项？
- 法务：WDI 指标中的第三方系列或不同标示如何影响其对应的数据集条款？
- 法务：WDI Dataset Terms 引用的 CC BY 4.0 法律文本（保存页面未收录）中，哪些条件适用于本来源的采集与再分发？
- Owner：WDI API 的 Basic Call Structure 页面（保存页面未收录）应由谁、在何时补存并核对请求容量与分页？
- Owner 与法务：两家提供方的保存页面均未涉及云端模型处理；具体处理或第三方传输边界需由谁、依据什么材料确认？
