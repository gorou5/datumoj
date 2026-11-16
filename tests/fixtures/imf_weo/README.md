# imf_weo：IMF WEO SDMX 2.1 离线样本

供 DATUMOJ-WISTERIA-302（IMF WEO → 观测值）离线测试使用。定稿见 `docs/plans/0.4-wisteria-sources.md` 2.1、4.1。

- 录制：2026-11-17（JST），User-Agent `DATUMOJ-fixture-recorder/0.1 (+https://github.com/gorou5/datumoj)`，每秒不超过 1 个请求，不跟随重定向，`Accept-Encoding: identity`，请求头 `Accept: application/vnd.sdmx.data+csv;version=1.0.0`（SDMX-CSV，每行带全部属性）。
- `https://api.imf.org/robots.txt` 返回 502，没读到规则。
- 每个文件的 URL、请求头、状态码、响应头（去掉 Set-Cookie 和服务器内部追踪头）、抓取时间、字节数、SHA-256 在 `_responses.json`。
- 完整性校验：本目录运行 `shasum -a 256 -c SHA256SUMS`。全部是 `captured`（原样保存）。

## 许可与署名

- IMF Copyright and Usage（<https://www.imf.org/en/About/copyright-and-terms>）：署名后可复制、派生、再分发，不得改动数值；本项目按非商业使用（2026-11-16 负责人定）。
- 署名：International Monetary Fund. World Economic Outlook (WEO), https://data.imf.org/en/datasets/IMF.RES:WEO. Accessed on 2026-11-17.

## 文件一览

| 文件 | 请求 | 用途（测试点） |
|---|---|---|
| `weo_GRC_GGXCNL_NGDP_2000-2015.csv` | `data/IMF.RES,WEO/GRC.GGXCNL_NGDP.A?startPeriod=2000&endPeriod=2015` | V07 当前版：2009 = -15.423608，`UNIT=PT`，`PUBLICATION_DATE=2026-10-13T02:00:00Z`，`LATEST_ACTUAL_ANNUAL_DATA=2025` |
| `weo_2026_apr_GRC_GGXCNL_NGDP_2000-2015.csv` | 同上，dataflow `WEO_2026_APR_VINTAGE` | 历史版：2009 = -15.423608，发布 2026-04-14 |
| `weo_2025_oct_GRC_GGXCNL_NGDP_2000-2015.csv` | 同上，dataflow `WEO_2025_OCT_VINTAGE` | 历史版：2009 = -15.424（只有 3 位小数），发布 2025-10-14，`LATEST_ACTUAL_ANNUAL_DATA=2024` |
| `weo_JPN-USA_NGDPD_2020-2030.csv` | `data/IMF.RES,WEO/JPN+USA.NGDPD.A?startPeriod=2020&endPeriod=2030` | #002 名义 GDP 美元：`OBS_VALUE` 已是全值、`SCALE=9` 只是显示量级；2026 年起是预测 |
| `weo_JPN_NGDP_2020-2024.csv` | `data/IMF.RES,WEO/JPN.NGDP.A?startPeriod=2020&endPeriod=2024` | #002 名义 GDP 本币 |
| `weo_bad_key.csv` | `data/IMF.RES,WEO/GRC.NO_SUCH_INDICATOR.A?...` | key 写错：200、只有表头，解析要报失败 |
