# DATUMOJ-IVY-501 完成说明

## 变更文件
- `src/datumoj/store/chunks.py`：`SqliteChunkStore`（ChunkStore）。documents / chunks / current_versions 三表；新快照设为当前版本，旧快照保留，可按 `snapshot_sha256` 取回；同一快照重写整体替换。
- `src/datumoj/index/fts.py`：`SqliteKeywordIndex`（KeywordIndex）。FTS5 `tokenize='trigram'`，索引 `title_path`、`text`（均先 `normalize`）；建库时探测 trigram，不支持抛 `RuntimeError`。
  - 3 字及以上：phrase 查询，`-bm25(title 权重 5, text 权重 1)` 作分数，标题命中优先。
  - 2 字以下：LIKE 扫描，`warnings` 标注；`like_limit`（默认 1000）限结果条数，`like_timeout`（默认 2 秒，用 progress handler 中断）超时返回已得部分并标注“查询过短且超时，结果不完整”。
  - 返回 `IndexResult`（按契约二对齐），不组装 Hit。
- `tests/index/test_fts.py`：继承 `ChunkStoreSuite`、`KeywordIndexSuite`，另有 34 条日文块的逐条命中、标题优先排序、来源过滤、替换、引号/`%`/`_` 字面量、LIKE 上限与超时、trigram 缺失报错。

## 验收
`make check T=tests/index/test_fts.py`：65 passed（最后一次改动后重跑过）。

## 未核对 / 风险
- LIKE 对 ASCII 以外的大小写不敏感由 Python 侧 `casefold` 计分，SQL 侧 LIKE 仅 ASCII 不区分大小写；日文场景无影响。
- 大语料下 LIKE 的实际耗时留给 H5 测量；默认 `like_timeout` 是估计值。
- 未新增依赖，未改契约。
