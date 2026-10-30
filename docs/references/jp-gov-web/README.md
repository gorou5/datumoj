# 政策网页参考包（jp-gov-web）

| 项目 | 内容 |
|---|---|
| 提供者 | 三个独立的种子页面：内閣府创新推进页面（S1；cao_seed.txt:2-7）、総務省地区数字化页面（S3；soumu_seed.txt:72-84）、大阪市经济战略局分类页面（S5；osaka_seed.txt:52-55）。 |
| 资料整理日期 | 2026-10-30（本次整理）。 |
| 资料核对日期 | 2026-10-30（核对本包所列的页面捕获文本）。 |
| 官方入口 | [内閣府种子页](https://www8.cao.go.jp/cstp/openinnovation/index.html)；[総務省种子页](https://www.soumu.go.jp/denshijiti/digital_transformation.html)；[大阪市种子页](https://www.city.osaka.lg.jp/keizaisenryaku/category/3037-1-0-0-0-0-0-0-0-0.html)。页面标题与正文指针见 notes.md 的 S1、S3、S5。 |
| 站点级规则 | 内閣府与総務省各自的页面分别写有带权利标记条件的 PDL1.0 表述（S2；cao_rule.txt:9；S4；soumu_tyosaku.txt:127）。大阪市的页面使用自己的数据利用与著作权页面说明（S7；osaka_use_data.txt:53-80；S8；osaka_copyright.txt:57-99）。三组记录互不替代，详见 terms.md。 |
| 单页与附件范围 | 本包未逐一检查目标单页及附件（包括 PDF）的权利。站点页面说明适用规则及其例外；具体目标页面或 PDF 是否带有其他标记为 NOT STATED（S2；cao_rule.txt:9,13-14；S4；soumu_tyosaku.txt:127,141-146；S7；osaka_use_data.txt:58,78-80；S8；osaka_copyright.txt:97-99）。 |
| robots.txt 观察 | 本方于 2026-10-30 的自有捕获记录：https://www8.cao.go.jp/robots.txt 返回 404，https://www.soumu.go.jp/robots.txt 返回 200，https://www.city.osaka.lg.jp/robots.txt 返回 404；総務省的 200 记录仅限首 600 字节内所见的唯一规则「User-agent: ia_archiver」（S10；soumu_robots.txt:2）及「Disallow: /」（S10；soumu_robots.txt:3）。记录日志本身不随参考包分发。以上仅为观察；404 不表示许可，任何结果均不推断抓取许可。 |
| 本资料边界 | 仅整理给定的已捕获文本；没有逐一核验页面附件或 PDF。本包不作许可推断，也不判断该抓取行为是否获准。SOURCE_CHECK_TABLE.md 为维护者专用侧车，不随公开参考包分发。公开参考包不提供逐项配置、未读材料或实际运行核验。 |

## 内容清单

- [notes.md](notes.md)：证据标签、三个种子页面、捕获记录、来源范围与未陈述事项。
- [terms.md](terms.md)：三个彼此分开的站点条款记录及其例外。
- SOURCE_CHECK_TABLE.md：维护者专用侧车，不属于公开参考包，不随公开参考包分发。
