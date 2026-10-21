# DATUMOJ-IVY-403 完成说明

## 变更

- CKAN 现在按最近一次全量的 `run_started` 检查分页快照及原始响应；只有分页连续并到达空页或短页，才把该次全量作为增量基准。
- 搜索页返回 304 时会读取该 URL 上一次成功快照的 raw 正文并继续翻页。
- 分页任务记录运行时的 `max_files`、`max_total_bytes` 和数据集 `limit`，用于识别上限导致的中断。
- 分页中断或请求失败时，下次运行改为全量，并在首个 `package_search` 任务的 `meta["incremental_fallback"]` 写入原因。该字段随快照保存，也会出现在 dry-run 报告的 `planned` 任务中。
- 成功的增量运行会推进后续 `since`；它不会改变最近一次全量的完整性判断。
- 新增基准测试覆盖完整全量、`max_files` / `limit` 中断、分页失败、连续增量及 dry-run 报告字段。

## 验收

```text
make check T=tests/crawlers/test_ckan_baseline.py
8 passed

make check T=tests/crawlers/test_ckan.py
22 passed
```

两条命令均因沙箱没有 `uv` 而使用仓库 `.venv` 运行，结果通过。

## 变更文件

- `src/datumoj/crawlers/ckan.py`
- `tests/crawlers/test_ckan_baseline.py`
- `results/DATUMOJ-IVY-403/summary.md`
- `results/DATUMOJ-IVY-403/tests.log`

未运行全仓库检查；未复核其他 crawler 的行为。历史 raw 缺失时无法直接核对正文，只有续页链完整且没有 `limit` 或上限中断证据时才接受；无法确认的单页历史运行会退回全量。
