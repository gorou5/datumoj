# DATUMOJ-IVY-401 完成说明

## 变更文件
- `src/datumoj/crawlers/ckan.py`（新增，`CkanCrawler`，kind=`ckan`）
- `sources/jp-egov-data.yaml`（新增）
- `tests/crawlers/test_ckan.py`（新增，22 个测试，含 `CrawlerSuite` 契约套件）
- `results/DATUMOJ-IVY-401/summary.md`

验收：`make check T=tests/crawlers/test_ckan.py` → 22 passed。

## 实现要点
- 分页：`package_search?rows&start&sort=id asc`，`start += 实际返回条数`，空页才停（不信 `count`，服务端截断 rows 也不会提前停）。
- 增量：有上次元数据运行（manifest 里 role=package_search/package_show 的成功快照，其 meta 带 `run_started`）时用
  `recently_changed_packages_activity_list`，按 `object_id` 去重、跳过 `deleted*`、只取比上次运行开始时间新的，逐个 `package_show`。
  活动列表不可用 / 非 success / 时间戳解析失败 / 窗口（`activity_limit`）不足以覆盖上次以来的变更时，退回全量，原因写进全量首页任务的
  `meta["incremental_fallback"]`（随快照进 manifest）。`scope.incremental: false` 可强制全量。
- 白名单（scope）：`formats`（NFKC、去点、小写后比较）、`organizations`（组织 name 或 title）、`max_file_bytes`（默认 50 MiB）；
  资源 host 必须在 `allowed_hosts` 内（`*.go.jp` 通配，与契约套件一致）。不在白名单的只留元数据（数据集快照里已有）。
- 许可：数据集级 `license_id` 不用作主要来源，取资源级 `license_id`（空串 / 空格 → None，其余原样，如 `CC-BY`），写进资源任务 `meta["license"]`。
- 失效链接：404 等由抓取器记为 `http_error`，不影响其他资源；`classify` 把「非 HTML 格式的资源却返回 HTML 页」（302 到 404 页）和
  API 的 `success:false` 判为 `empty_body`，不记成功。
- 数据集没变（`metadata_modified` 与上次成功下载时相同）的资源不重复下载。

## 单文件上限（要求 7）
① 声明的 `size` 超限的资源不生成下载任务；② `classify` 在实际正文 > `task.meta["max_file_bytes"]` 时返回 `FetchOutcome.too_large`
（测试覆盖「声明 10、实际 5000、上限 1000」）。下载中途中止仍由执行器按来源配置的抓取器上限兜底，这是契约的分工。

## 需要注意 / 没做的
- `drive_crawler`（`testing/fakes.py`，受保护）把 `classify` 返回的 `too_large` 直接套在带正文的结果上，会触发 `FetchResult` 校验失败
  （失败类结果不许有 content）。所以超限测试直接走 `plan → after_fetch → classify`，没用 `drive_crawler`。真实执行器（105）
  在应用 `too_large` 覆盖时需要丢弃正文并补 `error`。
- 「上次运行」的判定是按已成功的元数据快照取最晚的 `run_started`；若全量运行中途中断，中断前的页也会被算作上次运行。风险：增量可能漏掉未翻到的数据集，需要时用 `incremental: false` 补全量。
- `sources/jp-egov-data.yaml` 的 `max_files` / `max_total_bytes` / `formats` 是估计值，未对照真实站点规模核对；许可条款未逐项核对，保持 `internal_only` / `unverified`。
- 没有联网；没有 import 其他线的实现。
