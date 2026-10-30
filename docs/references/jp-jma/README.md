# 気象庁（jp-jma）参考资料

| 项目 | 内容 |
|---|---|
| 来源 | 気象庁（Japan Meteorological Agency）：防災情報 XML Atom feed 与平年値（读过官方页面（2026-10-20）；S7a、S9） |
| 提供者 | 37（jp-gov-data-api）团队（仅读文档；S3） |
| 资料整理日期 | 2026-10-20（仅读文档；S3） |
| 资料核对日期 | 2026-10-20（所核对的最新气象厅官方页面）（读过官方页面（2026-10-20）；S9） |
| 官方入口 | [XML/PULL 说明](https://xml.kishou.go.jp/xmlpull.html)；[平年値下载页](https://www.data.jma.go.jp/stats/data/mdrr/normal/index.html)（读过官方页面（2026-10-20）；S9） |
| 需要 Key | XML feed：资料称无需注册（读过官方页面（2026-10-20）；S9）；<br>平年値：下载页未显示注册或 Key 要求，其他要求未确认（读过官方页面（2026-10-20）；S9） |
| 许可 / 利用规约 | [气象厅网站利用说明](https://www.jma.go.jp/jma/kishou/info/coment.html)；[公共数据利用规约（第1.0版）](https://www.digital.go.jp/resources/open_data/public_data_license_v1.0)（读过官方页面（2026-10-20）；S9） |
| 可否再分发 | 部分 / 按文件核对；本包未附样本，因为这两份候选文件的第三方权利与排除项状态未能逐文件确认（仅读文档；S4） |
| 本资料的可信度 | 真实请求验证过：仅两份候选平年値文件的请求记录、字节数和 SHA-256 有证据；文件未随包提交（真实请求验证过；S4）。<br>官方页面读：XML、平年値及利用规约页面（读过官方页面（2026-10-20）；S9）。<br>仅读文档：格式表和 feed 说明（仅读文档；S2、S6、S7a）。<br>推测：仅在 notes.md 标出的采集建议与实现影响（推测；S7a、S8） |
| 对应的 DATUMOJ 任务 | UNVERIFIED：本资料未取得 DATUMOJ 对该来源的任务描述。（推测） |

## 内容清单

- README.md：来源、入口、核对边界与可信度。
- notes.md：feed 与平年値分开整理，列出已核实字段、格式、缺口及对 DATUMOJ 计划的对照。
- terms.md：利用规约、采集负载说明、robots.txt 观察结果及不附样本的理由。
- samples/：未创建；样本资格未通过逐文件权利核对。
