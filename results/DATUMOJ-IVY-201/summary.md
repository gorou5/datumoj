# DATUMOJ-IVY-201 完成说明（第 2 次尝试）

## 改动文件
- `src/datumoj/crawlers/egov_law.py`（新）：`EgovLawCrawler`（kind=`egov_law`）、`extract_law_xml`、`pending_amendments`
- `tests/crawlers/test_egov_law.py`（新）：契约套件 `CrawlerSuite` + 40 个用例
- `results/DATUMOJ-IVY-201/summary.md`

## 工作目录里没有上一次的代码
本次拉起时 worktree 是干净的，上一次的实现不在，所以按任务要求从头写，并处理了审查打回的问题。

## 审查打回的处理（要求 9）
- 实现了 `classify`：HTTP 200 但正文是 e-Gov 错误体（`{"code", "message"}`，含 404001 / 404004，且没有 `law_full_text` / `revisions` / `laws`）时返回 `FetchOutcome.empty_body`。
  - 不用 `http_error`：契约要求失败类 outcome 的 `content` 必须为空，而 `drive_crawler` 是在保留 content 的情况下覆盖 outcome。
  - `empty_body` 不是成功，`last_success` 不会返回它，所以下次运行会重试。
- `after_fetch` 对这类响应不再派生任务。
- 新增测试 `test_http_200_with_not_found_body_is_not_recorded_as_ok`：检查首次记录的 outcome 是 `empty_body`，并且下次重抓。

## 对照要求
1. `plan`：先抓 `/laws?limit=&offset=`，再按每部法令抓 `/law_revisions/{id}`，最后抓 `/law_data/{id}?asof=…&response_format=json&law_full_text_format=xml`。
2. `extract_law_xml`：非 JSON、非对象、缺字段、base64 非法、解码后不是 XML、错误体都抛带说明的 `ValueError`。
3. 增量：列表里现行版与最新版的 `(law_revision_id, updated)` 存进 `list_signature`，随 law_data 快照的 meta 保存。下次一致就跳过，不抓历史和正文；只有成功（ok / not_modified）的快照算数。
4. 时间口径：`asof` 取 `ctx.now` 的 JST 日期。`pending_amendments(revisions, asof)` 按施行日和 asof 比较，施行日为空时只认 `UnEnforced`。
5. `limit`：按列表中的全局位置截断，首页 `limit` 参数取 `min(page_size, limit)`，到达上限不再翻页。
6. 测试覆盖：分页、增量、单部 404、`extract_law_xml` 正常和损坏输入。
7. 翻页只看本页条数（0 条或少于 page_size 就结束），不依赖 `total_count` 和 `next_offset`。
8. 421 / 425 这类没有 `CurrentEnforced` 的法令，测试确认 `pending_amendments` 条数与样本 README 一致（3 / 6）。
9. 法令 ID 只取列表里给的值，并用 `[0-9A-Za-z_]+` 校验（`AC1` 形式可用）；404001 / 404004 都识别为不存在，HTTP 404 和 HTTP 200 两种形态都覆盖。
10. 施行基准日和未施行修改放进 `FetchTask.meta`，测试用 `drive_crawler` 驱动，并检查已写入 manifest。
11. meta 格式：`role`、`law_id`、`asof`、`pending_amendments` 为必有键；另有 `law_revision_id`、`list_signature`；每条未施行修改另带 `amendment_law_title`、`law_revision_id`。

## 跳过 / 风险
- 没核对真实网络（测试全部离线，样本回放）。
- `law_revision_id`（meta）取自列表里的现行版，而不是按 asof 重新推算；asof 等于运行当天时两者一致。
- 增量判断依赖 manifest 里的 `law_data` 快照 meta；`_previous` 缓存在 `plan` 开始时建立，每次运行重建。
- `scope.page_size` 为可选配置（默认 100）；`sources/jp-egov-law.yaml` 没有改（不在 scope）。
- 验收命令 `make check T=tests/crawlers/test_egov_law.py` 已跑：40 passed。
