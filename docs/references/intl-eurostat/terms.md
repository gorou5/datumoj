# 利用规约与采集注意：Eurostat（intl-eurostat）

## 证据来源

| 页面 | 保存页面涉及的章节或部分 |
|---|---|
| [Copyright notice and free re-use of data](https://ec.europa.eu/eurostat/help/copyright-notice) | General principles；Exceptions；How to re-use Eurostat material for commercial purposes；How to cite Eurostat products；Legal notice of the European Commission。[读过官方页面（2026-10-24）] |
| [API - Getting started](https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-getting-started) | Getting started；STATISTICS、SDMX 3.0、SDMX 2.1、CATALOGUE API；Additional information。[读过官方页面（2026-10-24）] |

## 按主题整理

| 主题 | 保存页面内容 |
|---|---|
| 一般复用 | 页面称网站发布的统计数据、元数据、出版物和其他传播工具可用于商业或非商业目的，但须注明来源；页面同时列出例外及个别 notice 条件。原文：「Reuse of statistical data, metadata, publications, and other dissemination tools published on this website for commercial or non-commercial purposes is authorised provided the source is acknowledged.」[读过官方页面（2026-10-24）] |
| 商业用途例外：来源与出版物 | 页面将明确标为属于 Eurostat 以外来源的数据列入例外；也将版权部分或全部属于其他组织的出版物或文件列入例外。页面对这组例外的表述为：「may not be reused for commercial purposes, but non-commercial reuse is possible without restriction」。[读过官方页面（2026-10-24）] |
| 商业用途例外：国家范围 | 页面列出 EU 成员国、EFTA 成员国及正式加入国和候选国以外国家的数据，并举美国、日本和中国为例；页面称商业再利用时需从表格中排除此类数据。[读过官方页面（2026-10-24）] |
| 商业用途例外：贸易数据 | 页面列出自 1995 年起、申报国为 Liechtenstein 或 Switzerland，且商品分类为 HS、SITC、BEC、NSTR 或国家商品分类的贸易数据；这些申报来源的出口/进口数据不得销售，但 Switzerland 数据由 EU 成员国申报时页面称可销售。页面另列申报国为 Austria、Combined Nomenclature 八位细度的数据不得销售；Austria 数据由其他 EU 成员国申报时页面称可销售。[读过官方页面（2026-10-24）] |
| 商业用途例外：程序 | 页面称商业用途没有特别程序或书面许可要求；材料仍受前述例外及个别 copyright notice 条件限制。[读过官方页面（2026-10-24）] |
| 署名与数据集引用 | 页面要求以数据集 DOI 和访问日期作引用；自定义数据集使用 Eurostat 数据集 datacode 链接和访问日期。原文格式：「Source: [digital object identifier (DOI) number of the Eurostat dataset], [access date]」及「Source: [Eurostat dataset datacode link], [access date]」。[读过官方页面（2026-10-24）] |
| 署名与其他产品引用 | 出版物格式以 European Commission、Eurostat、出版物标题、出版办公室、年份和链接组成；页面给出的格式片段为「European Commission, Eurostat, [Title of the publication], Publication Office of the European Union, [year]」。其他产品格式为「Source: Eurostat, [Title of the product], [link to the product], [access date].」。方括号项由使用者替换为具体信息。[读过官方页面（2026-10-24）] |
| 第三方权利与个别 notice | 页面称第三方作品可能需要另行取得权利；商标和标识不在一般许可内，但页面列出作为未修改 Eurostat 出版物组成部分再分发的例外。页面也称单项版权 notice 可规定条件，应查阅相关文档信息。[读过官方页面（2026-10-24）] |
| 修改、翻译与免责声明 | 页面要求翻译或修改数据、文本时向最终使用者说明，并加入 Eurostat 不承担责任的免责声明；翻译另列版权信息与译者责任说明。[读过官方页面（2026-10-24）] |
| 自动访问、批量下载与限流 | API 入门页描述 SDMX API 可下载完整数据集或子集，且完整请求可能需数分钟并可考虑异步方式；页面没有给出数值速率、并发、IP 限流或封锁规则，详细指南未保存。[待确认] |
| API 与 Key | 入门页列出四种 API 及 API 形式，但未说明 Key、注册或认证要求；Key 与认证状态待确认。[待确认] |
| 变更与可用性 | 版权 notice 称政策依据为 European Commission legal notice；该 legal notice 未保存。入门页记载其最后更新日为 2026-03-27，但没有给出服务可用性或底层数据更新承诺。[读过官方页面（2026-10-24）] |
| 云端模型处理 | 保存页面未涉及 AI/模型处理、向云端模型传输或相关授权；不得从沉默推断处理许可。[待确认] |

## 对 DATUMOJ 0.4 许可闸门的含义

- 保存页面给出站点级一般复用表述、署名条件和多类例外；单项版权 notice、未保存的 Commission legal notice 及具体数据项权利仍未核实，因此这些页面本身没有给出逐数据项的闸门结论。[待确认]
- 保存页面未涉及云端模型处理；文本不足以判断模型处理状态或将具体数据映射到 `outflow` 字段。[待确认]
