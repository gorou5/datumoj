# BODIK ODCS（jp-bodik）资料笔记

## 证据标签

| 标签 | 官方页面 URL | 取得日期 | 验证程度 |
|---|---|---|---|
| S1 | https://odcs.bodik.jp/about/ | 2026-10-20 | 读过官方页面（2026-10-20） |
| S2 | https://odcs.bodik.jp/developers/ | 2026-10-20 | 读过官方页面（2026-10-20） |
| S3 | https://odcs.bodik.jp/ | 2026-10-20 | 读过官方页面（2026-10-20） |
| S4 | https://odcs.bodik.jp/robots.txt | 2026-10-20 | 真实请求验证过 |
| S5 | https://data.bodik.jp/robots.txt | 2026-10-20 | 真实请求验证过 |
| S6 | https://data.bodik.jp/dataset/065007ae-d70c-4b47-afd3-8ef150800542 | 2026-10-20 | 读过官方页面（2026-10-20） |
| S7 | https://data.bodik.jp/api/3/action/package_show?id=402036_0000200_00031 | 2026-10-20 | 真实请求验证过 |

S1-S7 各标签指向本表列出的官方页面；取得日期见表。

## 1. 入口与利用规约

- BODIK ODCS 将自身描述为向地方自治体免费提供数据目录云服务。（读过官方页面（2026-10-20））〔证据:S1〕

> 「BODIK ODCSに掲載されているデータセットの利用規約は、各自治体が定める利用規約に依存します。」

> 原文链接：https://odcs.bodik.jp/about/；核对日期：2026-10-20。（读过官方页面（2026-10-20））〔证据:S1〕

- 页面说明各数据集的许可信息由各数据集分别显示；页面指向的显示位置与所读数据集页实际呈现位置不一致。（读过官方页面（2026-10-20））〔证据:S1〕〔证据:S6〕

> 「各データセットのライセンスについては、データセット表示の下方にある追加情報に記載されています。」

> 原文链接：https://odcs.bodik.jp/about/；核对日期：2026-10-20。（读过官方页面（2026-10-20））〔证据:S1〕

- 所读数据集页把 Creative Commons 表示显示在单独的「ライセンス」模块；后面的追加信息字段表不含许可字段。（读过官方页面（2026-10-20））〔证据:S6〕
- BODIK 页面将数据集利用规约交给各自治体；对某个自治体站点托管的资源文件，能否依据门户页面条款抓取仍待确认。（读过官方页面（2026-10-20））〔证据:S1〕
- 所查关于页面与开发者页面没有给出 BODIK 全站商业使用许可、统一署名句或目录元数据再分发许可。（仅读文档）〔证据:S1〕〔证据:S2〕

## 2. CKAN Action API 与 DataStore

> 「BODIK ODCSではデータカタログサイトのプラットフォームとしてCKANを利用しています。」

> 原文链接：https://odcs.bodik.jp/developers/；核对日期：2026-10-20。（读过官方页面（2026-10-20））〔证据:S2〕

- 开发者页面给出的 CKAN Site URL 是 https://data.bodik.jp/，并说明示例中的 demo.ckan.org 可替换为该站点。（读过官方页面（2026-10-20））〔证据:S2〕
- 页面列出数据集清单 API：/api/3/action/package_list；组织清单 API：/api/3/action/organization_list。（读过官方页面（2026-10-20））〔证据:S2〕
- package_search 示例包含 q=organization:402036；另一个关键词示例为 q=環境，文字说明默认返回 10 条。（读过官方页面（2026-10-20））〔证据:S2〕
- resource_search 示例的查询参数为 query=name:AED。（读过官方页面（2026-10-20））〔证据:S2〕
- 开发者页面写明表格数据文件可通过 DataStore 读取。（读过官方页面（2026-10-20））〔证据:S2〕

> 「CSVやXLSといった表形式のデータについては、ファイルの中身にアクセス可能になっています。」

> 原文链接：https://odcs.bodik.jp/developers/；核对日期：2026-10-20。（读过官方页面（2026-10-20））〔证据:S2〕

- 页面给出的 datastore_search 示例包含 resource_id 和 limit=5；所读页面没有给出最大 limit、offset 推进规则或按 _id 排序的说明。（读过官方页面（2026-10-20））〔证据:S2〕
- 一次 package_show 请求使用 id=402036_0000200_00031；该次响应顶层包含 success、result、help，且 success 为 true。（真实请求验证过）〔证据:S7〕
- 所读开发者页面链接到 CKAN API 手册；本资料仅记录该页面保存的调用示例，没有据此补写未见于该页面的分页上限或排序规则。（仅读文档）〔证据:S2〕
- 页面说明 CKAN 登记时间与 Web API 返回的时间均为 UTC，供日本使用时须换算为 JST。（读过官方页面（2026-10-20））〔证据:S2〕

> 「Web APIで取得した日時もUTCです。」

> 原文链接：https://odcs.bodik.jp/developers/；核对日期：2026-10-20。（读过官方页面（2026-10-20））〔证据:S2〕

## 3. 一次 package_show 响应所见字段

- 该次响应的 dataset 级字段为 license_id=cc-by、license_title=Creative Commons Attribution，并带有 license_url。（真实请求验证过）〔证据:S7〕
- 响应列出 13 个 resources；这些 resource 的 license_id 均为 null。（真实请求验证过）〔证据:S7〕
- 这一条响应不能说明 null 是继承 dataset 级许可、没有许可，或仅代表该字段未填写；其含义以及其他数据集是否填写 resource 级许可均待确认。（仅读文档）〔证据:S7〕
- 响应带有 metadata_created、metadata_modified，以及各资源的 created 和 last_modified 时间字段。（真实请求验证过）〔证据:S7〕
- 页面所示的这一数据集有作成日、最終更新和更新频度字段；其值分别显示为 2017-03-28 09:10、2026-09-16 18:38 和每月。（读过官方页面（2026-10-20））〔证据:S6〕
- 该数据集页面列出按年份命名的资源；这些名称不能单独证明文件内数据的覆盖期间。（读过官方页面（2026-10-20））〔证据:S6〕
- 上述更新频度与时间字段只描述所读的数据集和响应，不代表 BODIK 全站的更新安排。（仅读文档）〔证据:S2〕〔证据:S6〕〔证据:S7〕

## 4. robots.txt 与请求负载

- https://odcs.bodik.jp/robots.txt 的保存响应为 HTTP 200；User-agent: * 规则仅列出 /wp-content/uploads/*.pdf$。（真实请求验证过）〔证据:S4〕
- https://data.bodik.jp/robots.txt 的 User-agent: * 规则禁止访问 /user/、/admin/、/_tracking、/dataset/*?*、/dataset?*、/revision/ 与 /dataset/*/history。（真实请求验证过）〔证据:S5〕
- data.bodik.jp 的 robots 文件对一组被点名的 user-agent 列出 Crawl-delay: 2；该项不是 User-agent: * 规则。（真实请求验证过）〔证据:S5〕
- 另一组被点名的 user-agent 适用 Crawl-delay: 20，并被禁止访问 /api/ 与 /dataset/*?*。（真实请求验证过）〔证据:S5〕
- 该文件另对一组被点名的 user-agent 列出 Crawl-delay: 5。（真实请求验证过）〔证据:S5〕
- data.bodik.jp 文件中的注释提到 Nginx 端 1r/s 设置；注释没有确认该设置是否对所有调用者适用。（仅读文档）〔证据:S5〕
- 所查门户页面没有给出面向所有 API 使用者的速率数字；robots 文件中的延迟规则按 user-agent 分组，不能据此推定统一 API 速率。（仅读文档）〔证据:S2〕〔证据:S4〕〔证据:S5〕

## 5. 与 DATUMOJ 计划的对照

- docs/PLAN.md 将 jp-bodik 列为 P1，并写明复用 CKAN 采集器、DataStore 按 _id 排序且 offset 按实际返回行数推进、逐资源核对许可（多为 CC BY 4.0）。（仅读文档；docs/PLAN.md）
- BODIK 开发者页面确认使用 CKAN，并给出 DataStore 的 resource_id 与 limit 示例；保存页面没有说明 _id 排序或 offset 推进方式。（读过官方页面（2026-10-20））〔证据:S2〕
- 一次 package_show 响应显示 dataset 级 cc-by 标识，13 个资源的 license_id 为 null；这不能确认计划中多数数据集采用 CC BY 4.0 的说法是否适用于整个来源，也不能解释 null 的许可含义。（真实请求验证过）〔证据:S7〕
- 所查 BODIK 页面把数据集规约交给各自治体；具体资源的许可状态仍须按数据集核对。（读过官方页面（2026-10-20））〔证据:S1〕〔证据:S6〕

这些点可据此核对。
