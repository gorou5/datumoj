# 国际来源条款摘要

本表只归纳已保存的官方页面文本；许可适用范围未写明或相关条款未保存时标为待确认。[仅读文档]

| source id | 复用 / 再分发 / 商业用途 | 自动化访问 | 署名 | Key / 认证 | 配额 / 格式 / 更新 / 可用性 | 云端模型处理 | 待确认项 |
|---|---|---|---|---|---|---|---|
| intl-imf | 待确认；版权及使用条款未能读取，API 页未载许可条款。 | API 页仅说明 SDMX API 形式，未说明自动访问许可或限制。 | 待确认。 | Swagger 探索须 beta portal account 登录；数据 API Key 条件待确认。 | 数量、频率、格式、更新及封锁规则均待确认。 | 页面未涉及，待确认；cloud_model_ok 无依据。 | 复制、改编、再分发、商业用途、自动采集、云端处理及 API 限制。 |
| intl-oecd | 第 3 节写明：「Except where additional restrictions apply as stated above, you can extract from, download, copy, adapt, print, distribute, share and embed Data for any purpose, even for commercial use.」适用数据仍须核对 metadata 和第三方权利。 | API 提供访问方式；保存页面未给出数值配额，并保留调整单次查询数据量和一定时间内查询次数的权利，IP 可被限制或暂停。 | 使用数据自带 citation；没有特定 citation 时用「OECD (year), (dataset name),(data source) DOI or URL (accessed on (date)).」。 | 登记为自愿；Key 是否需要未说明。 | 单次数量、频次和格式可能变化；API 可暂停或终止，页面不承诺支持。 | 没有专门涉及，待确认；cloud_model_ok 无明确依据。 | 每个数据集的 metadata、第三方权利、云端模型处理及实际数值配额。 |
| intl-ilo | 页面称：「As of 3 May 2023, databases and datasets together with the accompanying referential metadata are covered by the Creative Commons CC BY 4.0 licence.」限定日期与微数据例外；较早数据不自动获 CC 许可。 | ILOSTAT 条款/API 文档未能读取；自动化条件待确认。 | Open Access policy 要求引用 ILO；统一数据引用格式待确认。 | ILOSTAT Key / 注册要求待确认。 | 数量、频率、格式、更新、支持与可用性均待确认。 | 页面未涉及，待确认；cloud_model_ok 无依据。 | 目标数据日期、排除项、第三方权利、ILOSTAT 条款/API 与云端处理。 |
| intl-comtrade | 第 5 节将「automated browsing or downloading, redistribution, publication, or commercial exploitation」列入限制，并称「is strictly prohibited without the prior written permission of the United Nations.」 | 明文限制自动浏览或下载；在作为自动采集来源前须向提供方确认 API 适用方式。 | 不得移除版权符号或作者信息；申请书面许可对象为联合国统计司司长。 | API Key 和免费/高级访问认证待确认。 | 数量、速率、格式、更新、IP 限制未说明；协议保留改变内容、功能与可用时段的权利。 | 页面未涉及，待确认；cloud_model_ok 无依据。 | 提供方对 API 自动采集的解释、免费/高级访问差异、API Key、数据免责声明及云端处理。 |
| intl-wto | 网站一般版权页称：「Other material such as official publications can be reproduced for non-commercial purposes provided attribution is given to the WTO and the WTO is informed.」并称：「Commercial use of materials from the website requires written permission from the WTO.」对 Stats/API 数据的适用范围待确认。 | API 门户描述程序化访问；未给出自动化权限或限流规则。 | 网站一般说明要求署名 WTO 并告知 WTO；固定格式待确认。 | Timeseries API 门户称：「Just sign up for an API key and start consuming APIs right away. It is free.」Key 权限与额度待确认。 | 配额、格式、更新、IP 限制、服务可用性和支持时限待确认。 | 页面未涉及，待确认；cloud_model_ok 无依据。 | 网站一般版权文本与 Stats/API 数据的关系、数据许可、云端处理及技术限额。 |

## 待 Owner 与法律顾问确认的问题

1. IMF：版权与使用条款正文目前未能读取；确认应取得哪些条款依据后，才能分别判断复制、改编、再分发、商业使用与自动采集边界。
2. OECD：拟选的具体数据集是否有额外 metadata 条件或第三方权利；请求频率和云端模型处理是否另有适用规则。
3. ILO：目标数据集的发布日期、微数据排除和第三方权利如何核对；ILOSTAT 专属条款与 API 文档需要哪些证据。
4. UN COMTRADE：在拟用的 API、访问层级和数据用途下，第 5 节关于自动浏览、下载和再分发的条款如何适用；应由哪个提供方角色确认。
5. WTO：网站一般版权页是否适用于 Stats Portal / Timeseries API 数据；数据本身、自动采集和再分发是否另有条款。
6. 五个来源：保存的页面均未明确云端模型处理；确认在取得更具体依据前，各来源的 cloud_model_ok 是否保持缺省 false。
