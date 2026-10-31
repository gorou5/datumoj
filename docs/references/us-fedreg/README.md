# Federal Register（us-fedreg）

| 项目 | 内容 |
|---|---|
| 提供者 | 37（jp-gov-data-api）团队 |
| 资料整理日期 | 2026-10-31 |
| 资料核对日期 | 2026-10-31 |
| 官方入口 | [REST API 说明](https://www.federalregister.gov/reader-aids/developer-resources/rest-api)（S4）；[交互式 API 文档](https://www.federalregister.gov/developers/documentation/api/v1)（S3）。维护者侧材料 `source.yaml:5` 配置的 API 根地址为 https://www.federalregister.gov/api/v1/；捕获页面正文未显示该路径。 |
| 需要 Key | 否；S3、S4 的说明页写明无需 API keys。（仅读文档） |
| 许可 / 利用规约 | 1 CFR § 2.6（https://www.ecfr.gov/current/title-1/chapter-I/subchapter-A/part-2/section-2.6；S1）；About This Site 的 Copyright 内容（https://www.federalregister.gov/reader-aids/government-policy-and-ofr-procedures/about-this-site；S2）；REST API 的 Usage Restrictions（https://www.federalregister.gov/reader-aids/developer-resources/rest-api；S4）。可否再分发：未核对。此处仅记录页面文字，结论由维护者 H2 检查决定；条款位置见 fedreg_1cfr_2_6.txt:153、fedreg_about_this_site.txt:101、fedreg_reader_aids_dev.txt:105。 |
| 本资料的可信度 | 仅读文档：S1-S4；已用真实请求验证：S5 robots.txt 响应形状（仅该响应本身）；推测：无。 |
| 对应的 DATUMOJ 任务 | DATUMOJ-MAPLE-201 |

## 内容清单

- notes.md：API 文档入口、Key 说明、分页资料、robots.txt 规则和未述事项。
- terms.md：条款摘要、引文、署名和第三方事项的未述范围、法律状态提示。
- 本包不含 samples/，没有提交捕获的 API 响应。
