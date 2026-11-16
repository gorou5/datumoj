# comtrade：UN Comtrade API **合成**样本

供 DATUMOJ-WISTERIA-305（UN Comtrade → 观测值）离线测试使用。定稿见 `docs/plans/0.4-wisteria-sources.md` 2.4、4.4。

**本目录所有数值都是合成的，不是 Comtrade 数据。**Comtrade 的使用政策只许本机构内部使用，真实数据不能进公开仓库。字段名和结构照官方 API 的响应（2026-11-17 用无 key 预览端点和 getDA 端点各请求一次取得结构，原响应不提交）。

- `data_<reporter>_<partner>_2022-2023.json`：模拟正式端点 `/data/v1/get/C/A/HS?reporterCode=<r>&partnerCode=<p>&period=2022,2023&flowCode=X,M&cmdCode=TOTAL&partner2Code=0&customsCode=C00&motCode=0` 的响应。每个文件 4 条（2 年 × X/M），金额是整数十亿的合成值；进口（M）的 `cifvalue` = `fobvalue` × 1.05、`primaryValue` 取 `cifvalue`，出口（X）的 `cifvalue` 为 null、`primaryValue` 取 `fobvalue`。
  - 6 个文件覆盖三对国家的双方：842↔156、276↔251、392↔410（Comtrade 自己的代码：美国 842、法国 251）。
  - `data_251_276_2022-2023.json` 多一条 reporter = partner = 251 的复进口行，解析时要排除。
- `getDA_<reporter>_2023.json`：模拟 `/public/v1/getDA/C/A/HS?reporterCode=<r>&period=2023`，`datasetCode`、`datasetChecksum`、`firstReleased`、`lastReleased` 都是合成值。
- `getDA_842_2023_revised.json`：同一报告方 checksum 和 `lastReleased` 变了，测“checksum 变了才重新拉”。
- `error_401_missing_key.json`：缺 key 时的 401 响应体（合成，措辞参照官方返回的 "missing subscription key"）。
- 完整性校验：`shasum -a 256 -c SHA256SUMS`。

## 许可

合成数据，不受 Comtrade 许可约束；字段名和结构来自官方 API 文档与响应。真实采集时署名 "Source: UN Comtrade."，许可等级 `no_outflow`。
