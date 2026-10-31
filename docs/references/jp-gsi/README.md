# 国土地理院標高 API（jp-gsi）

| 项目 | 内容 |
|---|---|
| 提供者 | 37（jp-gov-data-api）团队 |
| 资料整理日期 | 2026-10-21 |
| 资料核对日期 | 2026-10-20 |
| 官方入口 | [標高 API 说明](https://maps.gsi.go.jp/development/elevation_s.html)；[国土地理院内容利用规约](https://www.gsi.go.jp/kikakuchousei/kikakuchousei40182.html)；[测量成果利用手续](https://www.gsi.go.jp/LAW/2930-index.html)。 |
| 需要 Key | 未核对；API 说明页未列凭据参数，两次保存的请求未使用 Key。（S1、S10） |
| 许可 / 利用规约 | [公共数据利用规约（第1.0版）](https://www.digital.go.jp/resources/open_data/public_data_license_v1.0)；国土地理院内容利用规约称除另有说明外适用，但 API 响应是否属于其范围未决定。；[国土地理院内容利用规约](https://www.gsi.go.jp/kikakuchousei/kikakuchousei40182.html)；[测量成果利用手续](https://www.gsi.go.jp/LAW/2930-index.html)。可否再分发：未核对；API 响应的文件级再分发依据与测量法分类未确认。 |
| 本资料的可信度 | 已用真实请求验证：robots 响应及成功、无数据响应形状（S9、S10）；仅读文档：API 说明、利用规约、手续页面及法令文本（S1-S8）；推测：候选署名文字。 |
| 对应的 DATUMOJ 任务 |  |

保存的两种响应均无数据日期字段；请求时间不得替代数据日期。（真实请求验证过；S10）
API 说明页与手续页面未将该 API 响应归类。（读过官方页面（2026-10-20）；S1、S5）
DATUMOJ 项目讨论归属：unknown（本包未列出对应 Issue、PR 或计划行）。

## 内容清单

- notes.md：接口请求与响应形状、精度标记、日期边界、负载说明及计划对照。（仅读文档；S1、S10）
- terms.md：利用规约摘要、署名示例、测量法开放问题与不提供响应样本的决定。（仅读文档；S2、S3、S4、S5、S10）
- 本包不提供真实响应样本；两种响应的再分发状态待确认。（仅读文档；S2、S4、S5、S10）
