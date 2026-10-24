# 接口笔记：World Bank WDI（intl-worldbank-wdi）

## 证据与范围

- 本笔记只依据保存的 Dataset Terms、general Terms and Conditions 与 Indicators API 文章；没有进行 API 请求。[读过官方页面（2026-10-24）]
- [Terms of Use for Datasets](https://www.worldbank.org/ext/en/legal/terms-conditions/datasets)、[Terms and Conditions](https://www.worldbank.org/ext/en/legal/terms-conditions) 与 [About the Indicators API](https://datahelpdesk.worldbank.org/knowledgebase/articles/889392-about-the-indicators-api-documentation) 是本包所用页面。[读过官方页面（2026-10-24）]
- 保存的 Dataset Terms 文本以一段与条款正文无关的占位填充文字开头；这是保存页面的质量观察，不代表其后的条款内容。[读过官方页面（2026-10-24）]

## 1. API 形式与认证

- Indicators API 文章称需使用 V2 调用，并称 V1 已停止支持；旧端点会返回资源未找到的错误。[读过官方页面（2026-10-24）]
- 文章列出 World Development Indicators 为 Indicators API 可访问的数据库之一，并称 API 覆盖 45 个以上数据库和近 16,000 个时间序列指标。[读过官方页面（2026-10-24）]
- 文章称访问 Indicators API 不再需要 API key 或其他认证方式；此记录来自保存页面，未以真实请求验证。[读过官方页面（2026-10-24）]
- Dataset Terms 称 API 可用于帮助访问 Datasets；general Terms 的 API 章节另规定请求量和向使用者转授访问时的条件。[仅读文档]
- API 文章指向 Basic Call Structure 等文档，但这些 API 参考页未保存；具体路径、查询参数、过滤器、分页和错误格式待确认。[待确认]

## 2. 格式、限额与自动化访问

- Dataset Terms 称数据集以机器可读格式提供，但保存文本没有列出完整格式清单；Indicators API 文章提及可选择 JSON 输出。[读过官方页面（2026-10-24）]
- general Terms 要求 API 请求量保持合理，并禁止页面所称过量或滥用式使用；World Bank Group 可监测 API 使用，并保留对过量或滥用的判断权。[读过官方页面（2026-10-24）]
- 保存页面没有提供每次请求的数字上限、QPS、并发、IP 限流、封锁阈值或分页上限；具体请求容量待确认。[待确认]
- general Terms 要求通过 API 帮助他人访问 Materials 时，与使用者订立符合该 Terms 的约束性协议；Dataset Terms 还要求再许可包含相同署名要求。[读过官方页面（2026-10-24）]

## 3. 数据更新、支持与 WDI 范围

- Dataset Terms 页面标注最后更新日为 2018-03-23；general Terms 页面称其于 2025-07-25 修订。[读过官方页面（2026-10-24）]
- general Terms 称 API 可发布更新版本且新版本可能不兼容旧实现；Dataset Terms 也保留修改或停止数据集、网站及访问方式的权利。[读过官方页面（2026-10-24）]
- 保存页面未说明 WDI 数据集或每个指标的更新周期、修订历史完整性或稳定的版本保留行为。[待确认]
- Dataset Terms 与 general Terms 页面提供联系途径；具体联系方式不在本包转录，Indicators API 文章没有提供技术支持细则。[读过官方页面（2026-10-24）]
- Indicators API 文章列出 WDI，但 WDI 数据集及各指标的目录元数据没有保存；无法判断每个指标是否标注第三方来源或不同条件。[待确认]

## 4. 未能读取 / 未保存的页面

- 输入清单标明 API Basic Call Structure 和其他 API 参考页面未保存；端点参数、完整格式、分页、查询限额和认证细节仍待确认。[待确认]
- 输入清单标明 WDI 数据集目录元数据及每个指标的元数据未保存；第三方来源及其条件因此不能逐指标核实。[待确认]
- 输入清单标明 CC BY 4.0 法律文本未保存；保存的 Dataset Terms 只概述该许可并列附加条款，许可文本细节待确认。[待确认]
