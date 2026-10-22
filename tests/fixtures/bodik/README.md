# h1-bodik：BODIK ODCS（CKAN）离线样本

BODIK ODCS（https://data.bodik.jp/ ，自治体开放数据 CKAN 门户）接入用：通用 CKAN 采集器 `package_search` 分页 + `datastore_search` 分页读取（按 `_id` 升序，offset 按实际返回记录数推进）。

- 录制时间：2026-10-22（JST，UTC+9）；User-Agent `DATUMOJ/0.2 (+https://github.com/gorou5/datumoj; research fixture recording)`；每次请求间隔 ≥2.5 秒；`curl --compressed`，不跟随重定向。
- **API 基址**：`https://data.bodik.jp/api/3/action/{action}`（`package_search`、`datastore_search` 实测 200；`status_show` 返回 nginx 403，被站点屏蔽，采集器不要依赖它做健康检查）。
- **robots.txt**（`https://data.bodik.jp/robots.txt`，200）：`User-agent: *` 只禁 `/user/`、`/admin/`、`/_tracking`、`/dataset/*?*`、`/dataset?*`、`/revision/`、`/dataset/*/history`；**`/api/` 对一般 UA 不禁**（只对 Baiduspider、Bytespider、CCBot 等禁）。注释写明 Nginx 侧限速 1 r/s，并对主要爬虫要求 `Crawl-delay: 2`，采集器应按 ≥2 秒间隔。
- **字节说明**：文件内容就是 HTTP 响应体原样（仅去掉传输层 gzip），没有格式化；日文是 `\uXXXX` 转义。
- 校验：在本目录运行 `shasum -a 256 -c` 对照下表 SHA256。

## 文件一览

| 文件 | 完整请求 URL | 抓取时间（JST） | HTTP | 大小（字节） | SHA256 | 用途 |
|---|---|---|---|---|---|---|
| `package_search-p1.json` | `https://data.bodik.jp/api/3/action/package_search?q=%E5%85%AC%E5%85%B1%E6%96%BD%E8%A8%AD&fq=res_format:CSV&rows=3&start=0` | 2026-10-22T10:16:04+09:00 | 200 | 11477 | `b41904a27bf61dad99a2691ad704c08409c7c50d4882be3b543a6c484b22ef05` | 目录分页第 1 页（`count=1621`，3 个数据集，含本样本选用的读谷村数据集） |
| `package_search-p2.json` | `https://data.bodik.jp/api/3/action/package_search?q=%E5%85%AC%E5%85%B1%E6%96%BD%E8%A8%AD&fq=res_format:CSV&rows=3&start=3` | 2026-10-22T10:16:07+09:00 | 200 | 17056 | `f888c18b62e5e97aa17c26697ced69212b45d3716c02911a8610d7f00701c30c` | 目录分页第 2 页（`start=3`，3 个数据集；`start` 是记录偏移）；含 `datastore_active=false` 的 ZIP/PDF 资源，用来测过滤 |
| `datastore_search-p1.json` | `https://data.bodik.jp/api/3/action/datastore_search?resource_id=a6e50db4-8984-43b2-b477-99de98f8efab&limit=5&sort=_id%20asc&offset=0` | 2026-10-22T10:16:10+09:00 | 200 | 5160 | `b4336e5bdc5902a8d80c3b98b44d4113267f2c0c47b0ac85e740e9657edeb86d` | DataStore 第 1 页：`_id` 1–5，含 `fields`、`total=67`、`_links.next` |
| `datastore_search-p2.json` | `https://data.bodik.jp/api/3/action/datastore_search?resource_id=a6e50db4-8984-43b2-b477-99de98f8efab&limit=5&sort=_id%20asc&offset=5` | 2026-10-22T10:16:25+09:00 | 200 | 5232 | `821accaaa05e639b7ced7282801f3d268572e262550b2f922f3f804ee67ce44f` | 第 2 页：`_id` 6–10 |
| `datastore_search-p3.json` | `https://data.bodik.jp/api/3/action/datastore_search?resource_id=a6e50db4-8984-43b2-b477-99de98f8efab&limit=5&sort=_id%20asc&offset=10` | 2026-10-22T10:16:28+09:00 | 200 | 5335 | `03ea7a43d2c8505ec0d39c6a6f906f06e138d96d4c891fdb91a29cac57efb868` | 第 3 页：`_id` 11–15，`_links` 有 `prev` |
| `datastore_search-last.json` | `https://data.bodik.jp/api/3/action/datastore_search?resource_id=a6e50db4-8984-43b2-b477-99de98f8efab&limit=5&offset=65&sort=_id%20asc` | 2026-10-22T10:18:24+09:00 | 200 | 1350 | `1faa8649ce8c193f0742de3cd9a8c425210074f59b8abf52d811fc75e7f116da` | 最后一页不足 limit：`offset=65`，只有 2 条（`_id` 66、67）（主对话补录） |
| `datastore_search-end.json` | `https://data.bodik.jp/api/3/action/datastore_search?resource_id=a6e50db4-8984-43b2-b477-99de98f8efab&limit=5&sort=_id%20asc&offset=67` | 2026-10-22T10:16:31+09:00 | 200 | 2049 | `d7ebb38652c684988757b64efea5db347d984a9a620526bf73b4bdfeee1edb87` | 超出末尾：`offset=67`=`total`，`records=[]`，必须据此停止（`_links.next` 仍给出 offset=72，不能靠它判断结束） |

`package_search` 查询：`q=公共施設`（URL 编码）+ `fq=res_format:CSV`，`rows=3`。注意 `fq=res_extras_datastore_active:true` 在 BODIK 返回 0 条（该字段未进 Solr 索引），只能从 `resources[].datastore_active` 判断。

## 选用的 DataStore 资源

- 数据集：`473243_public_facility`「公共施設」，组织 `473243`「読谷村」（沖縄県），在 `package_search-p1.json` 第 1 条，无需单独 `package_show`。
- 资源：`a6e50db4-8984-43b2-b477-99de98f8efab`「公共施設」，CSV，`datastore_active: true`。
- **许可**：数据集级 `license_id=cc-by-40-intl`，`license_title=Creative Commons Attribution 4.0 International`，`license_url=https://creativecommons.org/licenses/by/4.0/deed.ja`；**资源级 `license_id`/`license_title` 均为 `null`**（BODIK 的许可在数据集级，与 e-Gov 样本“资源级有、数据集级 null”相反，解析时两级都要看）。
- **total = 67**（`total_was_estimated=false`），**不是 5 的倍数**：按 limit=5 分页时最后一页是 offset=65，只有 2 条（`datastore_search-last.json`，主对话补录），另录了 offset=67 的空页。
- 字段（`fields`，共 25 个，含 `_id`）：`_id`(int)、`都道府県コード又は市区町村コード`(numeric)、`NO`(numeric)、`都道府県名`、`市区町村名`、`名称`、`名称_カナ`、`名称_通称`、`POIコード`、`住所`、`方書`、`緯度`(numeric)、`経度`(numeric)、`電話番号`、`内線番号`、`法人番号`(numeric)、`団体名`、`利用可能曜日`、`開始時間`、`終了時間`、`利用可能時間特記事項`、`説明`、`バリアフリー情報`、`URL`、`備考`（未注明的为 text）。即政府「推奨データセット」公共施設格式。
- 响应顶层键：`_links`、`fields`、`include_total`、`limit`、`offset`、`records`、`records_format`、`resource_id`、`sort`、`total`、`total_estimation_threshold`、`total_was_estimated`。

## 分页观察

- p1/p2/p3 各 5 条，`_id` 连续 1–15，`offset`/`limit` 回显正确；end 页 `records=[]`、`total=67`。
- 数据本身的怪处（原样保留，可用作测试点）：`宇座公民館` 在 `_id=3` 与 `_id=6` 重复出现（同电话）；`_id=1` 長浜公民館 的 `緯度/経度` 为 35.38/136.26（不在沖縄，源数据错误）；`法人番号` 是 numeric 型（13 位整数）。

## 利用规约与再分发

- 页面：<https://odcs.bodik.jp/about/>「利用規約・ライセンス」节。要点：BODIK ODCS 上数据集的利用规约**取决于各自治体自己的利用规约**，许可写在各数据集的追加情报里；BODIK 本身不另设数据再分发限制。运营：公益財団法人九州先端科学技術研究所（ISIT）。
- 结论：本数据集许可为 CC BY 4.0，**允许再分发（含本仓库公开收录这些样本），须署名**，例：「出典：読谷村『公共施設』（BODIK ODCS, https://data.bodik.jp/），CC BY 4.0」。上两页里其他数据集多为 CC BY 4.0，另有 `cc-by-21-jp`（小値賀町）、`cc-by`（佐世保市），也都是署名类。未逐条核对読谷村自身的利用规约。

## 个人信息检查

- 已检查：所有响应中无邮箱地址；`author`/`maintainer` 只有自治体名或课名；`author_email`/`maintainer_email` 为 `null`。`creator_user_id` 是账号 UUID，原样保留。
- DataStore 记录是公民館、集会所、自治会等设施，`電話番号` 为设施代表电话（098-958-xxxx），**不是个人名册**。`_id=15`「横田自治会」是团体名，非个人。未发现个人信息。

## 请求记录

共 14 次请求（≤30）。进入本目录 6 个；其余为探索/核对，未收录：
- `https://data.bodik.jp/robots.txt` → 200（2026-10-22T10:15:26+09:00）
- `https://data.bodik.jp/api/3/action/status_show` → 403（2026-10-22T10:15:29+09:00）
- `https://data.bodik.jp/api/3/action/package_search?rows=0` → 200（2026-10-22T10:15:36+09:00）
- `https://data.bodik.jp/api/3/action/package_search?q=%E5%85%AC%E5%85%B1%E6%96%BD%E8%A8%AD&fq=res_extras_datastore_active:true&rows=3&start=0` → 200（2026-10-22T10:15:46+09:00）
- `https://data.bodik.jp/api/3/action/package_search?q=%E5%85%AC%E5%85%B1%E6%96%BD%E8%A8%AD&fq=res_format:CSV&rows=3&start=0` → 200（2026-10-22T10:15:53+09:00）
- `https://data.bodik.jp/` → 200（2026-10-22T10:16:45+09:00）
- `https://odcs.bodik.jp/robots.txt` → 200（2026-10-22T10:16:55+09:00）
- `https://odcs.bodik.jp/about/` → 200（2026-10-22T10:17:09+09:00）
