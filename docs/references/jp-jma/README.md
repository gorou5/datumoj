# 気象庁（jp-jma）参考资料

| 项目 | 内容 |
|---|---|
| 提供者 | 37（jp-gov-data-api）团队 |
| 资料整理日期 | 2026-10-20 |
| 资料核对日期 | 2026-10-20 |
| 官方入口 | [XML/PULL 说明](https://xml.kishou.go.jp/xmlpull.html)；[平年値下载页](https://www.data.jma.go.jp/stats/data/mdrr/normal/index.html)。 |
| 需要 Key | XML feed：否（说明页称无需注册）；平年値：未核对（下载页未显示注册或 Key 要求）。 |
| 许可 / 利用规约 | [公共数据利用规约（第1.0版，PDL1.0）](https://www.digital.go.jp/resources/open_data/public_data_license_v1.0)。；[气象厅网站利用说明](https://www.jma.go.jp/jma/kishou/info/coment.html)；页面提示查看气象厅专属的重要信息页。可否再分发：未核对；两份候选文件的逐文件第三方权利与排除项状态未确认，本包未附样本。 |
| 本资料的可信度 | 已用真实请求验证：两份候选平年值文件的请求记录、字节数和 SHA-256（S4）；仅读文档：XML、平年值与利用规约页面（S9），格式与 feed 说明（S2、S6、S7a）；推测：notes.md 标出的采集建议与实现影响（S7a、S8）。 |
| 对应的 DATUMOJ 任务 | DATUMOJ-IVY-203：覆盖气象厅防灾信息 XML 长期 feed；平年值部分仅作参考资料。 |
| 来源 | 気象庁（Japan Meteorological Agency）：防災情報 XML Atom feed 与平年値（读过官方页面（2026-10-20）；S7a、S9） |

## 内容清单

- README.md：来源、入口、核对边界与可信度。
- notes.md：feed 与平年値分开整理，列出已核实字段、格式、缺口及对 DATUMOJ 计划的对照。
- terms.md：利用规约、采集负载说明、robots.txt 观察结果及不附样本的理由。
- samples/：未创建；样本资格未通过逐文件权利核对。
