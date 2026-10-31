# Jグランツ 补助金信息取得 API（jp-digital-jgrants）资料笔记

## 证据标签

S1–S4 指向本次阅读的保存文本。S1、S2 是 PDF 正文抽取文本，S3 是门户服务规约页面节选，S4 是维护者提案而非官方来源。URL 取自相应捕获文件第 1 行的头部元数据，仅作来源入口，不作为页面正文引文。

| 标签 | 官方页面或保存材料 | 验证级别 |
|---|---|---|
| S1 | jGrants Web-API 利用规约 PDF：<https://fs2.jgrants-portal.go.jp/API%E5%88%A9%E7%94%A8%E8%A6%8F%E7%B4%84.pdf>。 | 读过官方文档（2026-10-30） |
| S2 | Jグランツ 补助金信息取得 API 利用概要 PDF：<https://fs2.jgrants-portal.go.jp/API%E5%88%A9%E7%94%A8%E6%A6%82%E8%A6%81.pdf>。 | 读过官方文档（2026-10-30） |
| S3 | Jグランツ 利用规约页面：<https://www.jgrants-portal.go.jp/stipulation>。 | 读过官方页面（2026-10-30） |
| S4 | datumoj issue 48 的维护者提案文本：<https://github.com/gorou5/datumoj/issues/48>；非官方来源。 | 仅读文档 |

本文件中的 `文件名:行号` 指向未随本包提交的本地保存记录（公开仓库不提交未核对再分发范围的原文）；读者核对时以各标签对应的官方 URL 为准。

### F 标签

F2：记录出处标注与编辑加工要求。

F3：记录使用者类别及使用条件。

F4：记录禁止事项、访问限制、停止使用及责任等规约事实。

F5：记录第三方权利内容及使用者需自行确认或取得许可的事实。

## 1. 请求形状

- S2 将接口方式列为 REST，请求格式列为 GET，并列出 JSON 响应与 SSL 支持。（F1；读过官方文档（2026-10-30）；S2；api_overview.txt:12, api_overview.txt:14, api_overview.txt:15, api_overview.txt:16）
- S2 写明不要求利用申请、没有认证功能，并给出调用次数上限；Key 是否作为单独凭据字段说明未载明。（F1；S2；api_overview.txt:17, api_overview.txt:18, api_overview.txt:19, api_overview.txt:20）
- S4 列出 API 基址、三个路径和部分参数；这些接口路径与参数在本次所读 S1–S3 中未核实。此处仅为维护者自述，未由本资料核实。（S4；datumoj_issue48_body.md:17, datumoj_issue48_body.md:18, datumoj_issue48_body.md:22, datumoj_issue48_body.md:24, datumoj_issue48_body.md:25, datumoj_issue48_body.md:26, datumoj_issue48_body.md:73）

## 2. 响应字段与数据形状

- S2 将输出概述为补助金基本信息与详细信息，并列出字段类别，包括 ID、名称、用途、行业、目标区域、金额上限、募集期间等。（S2；api_overview.txt:7, api_overview.txt:8, api_overview.txt:9, api_overview.txt:10, api_overview.txt:11）
- S2 列明响应格式为 JSON。（S2；api_overview.txt:15）
- S4 提案描述 JSON、详情中包含 PDF 的 base64，以及 V2 的年度／轮次 workflow 字段；均是维护者提案内容，未由本资料核实。（S4；datumoj_issue48_body.md:28, datumoj_issue48_body.md:30, datumoj_issue48_body.md:87, datumoj_issue48_body.md:94）
- S4 自述曾对一览与详情 V2 发出请求并收到成功响应；这是维护者自述，未由本资料核实，本次未发起 API 请求。（S4；datumoj_issue48_body.md:47, datumoj_issue48_body.md:49, datumoj_issue48_body.md:50, datumoj_issue48_body.md:51）

## 3. 服务状态、调用限制与日期

- S2 写明调用次数受限，上限为每秒 10 次；材料未给出日量、封锁时长或重试数值。（F1；S2；api_overview.txt:19, api_overview.txt:20）
- S1 称本功能为 beta，并保留不预先通知即停止、休止、中断或限制使用的条款。（F4；S1；api_terms.txt:9, api_terms.txt:91, api_terms.txt:92, api_terms.txt:93, api_terms.txt:94）
- S1 与 S2 的正文没有显示制定日期。文件头部所列 PDF 元数据日期不是规约或概要正文的制定日期，不用作文件日期。（F8；S1；api_terms.txt:2；S2；api_overview.txt:2）
- S3 页面显示制定日为 2020-12-24，改订日为 2021-09-01 与 2025-09-26。（F8；S3；portal_stipulation_excerpt.txt:9, portal_stipulation_excerpt.txt:10, portal_stipulation_excerpt.txt:11）
- S1–S3 对 API 输出的数据更新频率、更新机制及数据日期字段均未载明；逐项开放事项见 terms.md。（F6；api_terms.txt:20, api_terms.txt:21；api_overview.txt:7, api_overview.txt:11, api_overview.txt:21, api_overview.txt:22；portal_stipulation_excerpt.txt:25, portal_stipulation_excerpt.txt:64）

## 4. 与 DATUMOJ 提案的对照

- 字段对照表记录每项提案值、官方材料支持状态、原文位置与短引文；提案中的实测叙述仍按维护者自述处理。（S4；datumoj_issue48_body.md:47, datumoj_issue48_body.md:61, datumoj_issue48_body.md:87, datumoj_issue48_body.md:89, datumoj_issue48_body.md:96, datumoj_issue48_body.md:98）
- API 输出是否适用门户服务规约未由现有材料解决；不把门户一般条款直接推定为 API 输出许可。（F7；api_terms.txt:20, api_terms.txt:21；portal_stipulation_excerpt.txt:25, portal_stipulation_excerpt.txt:37, portal_stipulation_excerpt.txt:64；datumoj_issue48_body.md:18）
