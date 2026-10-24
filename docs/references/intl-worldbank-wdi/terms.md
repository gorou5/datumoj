# 利用规约与采集注意：World Bank WDI（intl-worldbank-wdi）

## 证据来源

| 页面 | 保存页面涉及的章节或部分 |
|---|---|
| [Terms of Use for Datasets](https://www.worldbank.org/ext/en/legal/terms-conditions/datasets) | Description；Licenses；Exceptions for Some Third-Party Data；Other。[读过官方页面（2026-10-24）] |
| [Terms and Conditions](https://www.worldbank.org/ext/en/legal/terms-conditions) | Notice；Dataset-specific terms；remainder of Materials；Application Program Interfaces；Miscellaneous。[读过官方页面（2026-10-24）] |
| [About the Indicators API](https://datahelpdesk.worldbank.org/knowledgebase/articles/889392-about-the-indicators-api-documentation) | How to use the V2 Indicators API；API Access / Authentication。[读过官方页面（2026-10-24）] |

## 按主题整理

| 主题 | 保存页面内容 |
|---|---|
| 条款层次 | Dataset Terms 适用于 World Bank Data Catalog 中列出的 Datasets，并纳入 general Terms；general Terms 也指出部分 Materials 有专门条款。两者冲突时 Dataset Terms 优先，原文：「Where these Dataset Terms conflict with the general Terms and Conditions, these Dataset Terms shall prevail.」[读过官方页面（2026-10-24）] |
| Dataset Terms 的一般用途与许可 | Dataset Terms 称数据集免费提供；除非特别标示，按 CC BY 4.0 提供并附加争议解决条款。该附加条款描述调解及在 45 天内未解决时可转入仲裁的程序。原文：「Unless specifically labeled otherwise, these Datasets are provided to you under a Creative Commons Attribution 4.0 International License (CC BY 4.0)」。页面还称可提取、下载、复制和按条款与第三方分享数据集；数据集衍生的表格、图表也受 Dataset Terms 约束。[读过官方页面（2026-10-24）] |
| Dataset Terms 的第三方分享与商业用途 | 页面把复制和向第三方分享数据集置于 Dataset Terms 约束之下，并要求署名与再许可条件；保存页面没有另列 Dataset Terms 下的一般商业用途禁止条款。具体 WDI 指标是否另有标示或第三方条件未核实。[仅读文档] |
| general Terms 对其他 Materials 的条件 | general Terms 将特定 Dataset 条款与 remainder of Materials 区分；对 remainder of Materials 页面说明非商业使用，并对衍生作品和商业使用设限，除非相关机构事先书面同意。原文：「For the remainder of the Materials, you may make non-commercial uses thereof」。这些文字与 Dataset Terms 的适用范围需分层记录。[读过官方页面（2026-10-24）] |
| 署名格式 | Dataset Terms 要求署名 World Bank 与数据提供方，格式为：「The World Bank: Dataset name: Data source (if known).」[读过官方页面（2026-10-24）] |
| 再许可署名 | 分享或帮助他人访问 Datasets 时，再许可须包含相同的 acknowledgment 要求，并要求下游再许可者也如此；页面称可提供条款页面的 URL 满足该呈现要求。[读过官方页面（2026-10-24）] |
| 第三方权利与 WDI 指标 | Dataset Terms 称一些数据集或指标来自第三方，可能未经原提供方同意不得再分发或复用，或适用不同条件；适用情况记在数据集或指标元数据中。该元数据未保存，任何具体 WDI 指标的来源和条件待确认。[读过官方页面（2026-10-24）] |
| 自动化访问与 API 请求 | general Terms 的 API 章节禁止超过合理请求量或页面所称过量、滥用式使用，并称 World Bank Group 可监测 API 使用。原文片段：「exceeds reasonable request volume or constitutes excessive or abusive usage」。页面没有数字速率、请求大小或并发阈值。[读过官方页面（2026-10-24）] |
| 使用 API 转供他人访问 | Dataset Terms 称 API 可用于帮助访问 Datasets，原文片段：「You may use our application programming interfaces (“APIs”) to facilitate access to the Datasets」。general Terms 要求通过 API 帮助他人访问 Materials 时与使用者订立符合条款的约束性协议。[读过官方页面（2026-10-24）] |
| API Key 与版本 | Indicators API 文章称：「API keys and other authentication methods are no longer necessary to access the API.」文章要求使用 V2；具体端点及 Basic Call Structure 未保存。[读过官方页面（2026-10-24）] |
| 变更与可用性 | Dataset Terms 页面标注最后更新日为 2018-03-23，general Terms 页面称于 2025-07-25 修订。Dataset Terms 保留修改或停止数据集及访问方式的权利；general Terms 称 API 新版本可能不兼容旧实现。[读过官方页面（2026-10-24）] |
| 云端模型处理 | 保存页面未涉及 AI/模型处理、向云端模型传输或相关授权；第三方分享和转供访问条款没有明确讨论云端模型处理。[待确认] |

## 对 DATUMOJ 0.4 许可闸门的含义

- 保存文本分别说明 Dataset Terms 的一般条件、general Terms 对 remainder of Materials 的条件，以及第三方数据可能适用的例外；WDI 是否全部由标准 Dataset Terms 覆盖、各指标是否有第三方标注，均待元数据核对。[待确认]
- 保存页面未涉及云端模型处理；文本不足以判断模型处理状态，也不足以将来源映射到 `outflow` 或 `cloud_model_ok` 的具体值。[待确认]
