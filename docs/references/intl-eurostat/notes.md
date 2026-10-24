# 接口笔记：Eurostat API（intl-eurostat）

## 证据与范围

- 本笔记只依据保存的版权 notice 与 API 入门页面；保存内容为页面正文文本，不是完整 API 规格。[读过官方页面（2026-10-24）]
- [Copyright notice and free re-use of data](https://ec.europa.eu/eurostat/help/copyright-notice) 与 [API - Getting started](https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-getting-started) 是本包所用的官方页面；两页的保存时间见输入清单。[读过官方页面（2026-10-24）]

## 1. 接口与入口形式

- 入门页列出四个 REST API：STATISTICS（Eurostat JSON web service 的延续）、SDMX 3.0、SDMX 2.1，以及用于数据集发现的 CATALOGUE。[读过官方页面（2026-10-24）]
- STATISTICS API 面向 Eurostat 可视化工具的数据查询并称开放供公众使用；它使用 REST，提供 JSON-stat 2.0，支持 CORS，响应语言为英语、法语或德语。[读过官方页面（2026-10-24）]
- SDMX 3.0 与 SDMX 2.1 API 支持元数据和数据查询，可列出公开数据集、取得数据集结构，并下载完整数据集或子集。[读过官方页面（2026-10-24）]
- CATALOGUE API 用于以程序方式发现数据集；入门页还将 Eurostat Table of Contents XML 与 DCAT Application Profile 的 RDF 目录发现方式列出。[读过官方页面（2026-10-24）]
- 页面说明 `<api_base_uri>` 依提供 API 的组织而定；允许值为 Eurostat=`ec.europa.eu/eurostat/api/dissemination`、DG EMPL=`webgate.ec.europa.eu/empl/redisstat/api/dissemination`、DG GROW=`webgate.ec.europa.eu/grow/redisstat/api/dissemination`、DG TAXUD=`webgate.ec.europa.eu/taxation_customs/redisstat/api/dissemination`。[读过官方页面（2026-10-24）]

## 2. 格式、大小与处理时间

- STATISTICS API 的格式为 JSON-stat 2.0；该页面没有列出该 API 的 CSV 或 XML 格式。[读过官方页面（2026-10-24）]
- SDMX 3.0 API 除 SDMX-ML 3.0 外还列出 SDMX-CSV 2.0、JSON-stat 2.0、TSV 和 SDMX-ML 2.1。[读过官方页面（2026-10-24）]
- SDMX 2.1 API 除 SDMX-ML 2.1 外还列出 SDMX-CSV 1.0、JSON-stat 2.0 和 TSV。[读过官方页面（2026-10-24）]
- 页面称小型子集查询可能返回数 KB 并在数秒内完成；完整数据集可能返回数 MB，并在数分钟后完成；这些是页面描述的响应和处理时间，不是公布的硬性查询上限。[读过官方页面（2026-10-24）]
- 页面称对于完整数据集请求，可考虑使用异步请求；具体异步流程在未保存的详细指南中。[读过官方页面（2026-10-24）]
- 保存页面没有提供量化的每次查询数据行数、并发数、QPS、IP 限流或封锁阈值；未保存的详细指南和具体数据集页面仍待核对。[待确认]

## 3. Key、更新、支持与采集注意

- 两个保存页面没有说明 API Key、注册或登录要求；这不证明不需要 Key，状态待确认。[待确认]
- API 入门页面标示最后更新日为 2026-03-27；页面未说明底层数据集的更新周期、修订通知或版本保留行为。[读过官方页面（2026-10-24）]
- 版权 notice 指示其他版权问题可联系其版权单位；保存的 API 入门页没有给出技术支持渠道或 API 服务可用性承诺。[读过官方页面（2026-10-24）]
- 采集端需按所选 API 的文档和组织对应值构造请求；由于详细指南未保存，具体查询参数、分页、异步轮询和请求上限均待确认。[推测]
- 版权 notice 表示个别文档的版权 notice 可增加条件；下游在确定具体数据产品后仍需核对其单项 notice。[读过官方页面（2026-10-24）]

## 4. 未能读取 / 未保存的页面

- 输入清单标明 European Commission legal notice 未保存；这使页面所称许可政策基础的具体条文仍待确认。[待确认]
- 输入清单标明 Statistics、SDMX 3.0、SDMX 2.1 与 Catalogue 的详细 API 指南未保存；因此请求参数、并发、查询大小、速率、封锁条件和异步请求细节仍待确认。[待确认]
- 输入清单标明逐数据集版权 notice 或元数据未保存；因此特定产品的来源权利、例外及可再利用范围仍待确认。[待确认]
