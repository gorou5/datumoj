# oecd_sdmx：OECD SDMX 离线样本

供 DATUMOJ-WISTERIA-303（OECD SDMX → 观测值）离线测试使用。定稿见 `docs/plans/0.4-wisteria-sources.md` 2.2、4.2。

- 录制：2026-11-17（JST），User-Agent `DATUMOJ-fixture-recorder/0.1 (+https://github.com/gorou5/datumoj)`，每秒不超过 1 个请求（4 次请求，远低于每小时 60 次），不跟随重定向，`Accept-Encoding: identity`；数据用 `format=csv`。
- `https://sdmx.oecd.org/robots.txt` 返回 404，没有规则。
- 每个文件的 URL、状态码、响应头、抓取时间、字节数、SHA-256 在 `_responses.json`。完整性校验：`shasum -a 256 -c SHA256SUMS`。全部是 `captured`。

## 许可与署名

- OECD Terms and Conditions（<https://www.oecd.org/en/about/terms-conditions.html>）第 3 节：数据可提取、复制、改编、分发，须按 OECD 格式署名。日本工资数据的原始来源是厚生劳动省（第三方数据）。本项目对 OECD 先按 `no_outflow`；样本只有几十个值，作测试用。
- 署名（逐个数据集，URL 为下表的请求 URL）：
  - OECD (2026), `OECD.SDD.NAD:DSD_NAMAIN10@DF_TABLE1_EXPENDITURE(2.0)`, https://sdmx.oecd.org/public/rest/dataflow/OECD.SDD.NAD/DSD_NAMAIN10@DF_TABLE1_EXPENDITURE/2.0, accessed on 2026-11-17.
  - OECD (2026), `OECD.SDD.STES:DSD_STES_REVISIONS@DF_STES_REVISIONS(4.0)`, https://sdmx.oecd.org/public/rest/dataflow/OECD.SDD.STES/DSD_STES_REVISIONS@DF_STES_REVISIONS/4.0, accessed on 2026-11-17.
  - OECD (2026), `OECD.SDD.TPS:DSD_EAR@DF_HOU_EAR(1.0)`, https://sdmx.oecd.org/public/rest/dataflow/OECD.SDD.TPS/DSD_EAR@DF_HOU_EAR/1.0, accessed on 2026-11-17.
  - 数据集自带署名（dataflow 注记）以 303 实现时读到的为准，有就优先用它。

## 文件一览

| 文件 | 请求 | 用途（测试点） |
|---|---|---|
| `namain10_JPN-USA_2020-2024.csv` | `data/OECD.SDD.NAD,DSD_NAMAIN10@DF_TABLE1_EXPENDITURE,2.0/A.JPN+USA.S1.S1.B1GQ._Z._Z._Z.XDC+USD_EXC.V.N.T0102?startPeriod=2020&endPeriod=2024&format=csv` | #002 名义 GDP，20 行；`UNIT_MULT=6`、`CURRENCY` 区分本币和美元 |
| `stes_revisions_JPN_201901-201902.csv` | `data/OECD.SDD.STES,DSD_STES_REVISIONS@DF_STES_REVISIONS,4.0/JPN.M.H_EARN.IX.C.201901+201902?startPeriod=2017-10&endPeriod=2018-03&format=csv` | V13 修订库：2018-01 在 EDITION 201901 为 81.41、201902 为 81.51 |
| `hou_ear_JPN_2018.csv` | `data/OECD.SDD.TPS,DSD_EAR@DF_HOU_EAR,1.0/JPN.EAR.IX.N.C.S1.M?startPeriod=2018-01&endPeriod=2018-12&format=csv` | V13 当前值 |
| `contentconstraint_CR_A_DF_TABLE1_EXPENDITURE.xml` | `contentconstraint/OECD.SDD.NAD/CR_A_DSD_NAMAIN10@DF_TABLE1_EXPENDITURE/` | 发布标识：`validFrom="2026-11-16T07:54:30Z"` |
