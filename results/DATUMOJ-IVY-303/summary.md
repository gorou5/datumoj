# DATUMOJ-IVY-303 完成说明

## 变更文件
- `src/datumoj/store/observations.py`（新）：`DuckDBObservationStore(root)`
- `tests/store/test_observations.py`（新）：继承 `ObservationStoreSuite`，外加 51 条本实现的测试
- `results/DATUMOJ-IVY-303/summary.md`（本文件）

## 验收
- `make check T=tests/store/test_observations.py` → `83 passed in 1.94s`
- 全量 `.venv/bin/pytest -q` → `607 passed`

## 做了什么
1. **存储**：`<root>/parquet/<source_id>/<sha256(table_key)[:32]>.parquet`，一张表一个文件。先写临时文件，再 `os.replace` 整体替换；空列表就删掉文件。
   文件名用哈希：大小写不敏感的文件系统（macOS 默认就是）上 `T1`/`t1` 不会互相覆盖，`../`、`/` 也走不出目录。source_id 按契约的 `SourceId` 校验。
   `value` 统一存成 `DECIMAL(38,10)`，所有文件同一类型，DuckDB 合并多个文件时不会悄悄转换类型、截断数值。
2. **查询的两层防护**
   - 白名单 `_guard`：分词和解析都用 DuckDB 自己的（`duckdb.tokenize`、`duckdb.extract_statements`），不自己写正则切字符串和注释（`E'\''`、`$$…$$`、注释里藏 `;` 这类歧义就不会出现）。
     要求：第一个关键字是 SELECT/WITH，不能有第二条语句，解析结果必须恰好一条 `StatementType.SELECT`。按名字拒绝读文件、连外部库和执行 SQL 串的函数（`read_*`、`*_scan`、`glob`、`parquet_*`、`query`、`query_table` 等），带引号、改大小写、加 schema 前缀都拦得住。
   - 引擎层 `_connect`：以 `read_only=True` 打开 `<root>/observations.duckdb`（一个空库），建 TEMP VIEW `observations`，`allowed_paths` 只放行本库的 Parquet 文件。然后关闭扩展自动安装和自动加载，`enable_external_access=false`，最后 `lock_configuration=true`。
     测试直接拿这个连接执行 `SET enable_external_access=true`、`RESET …`、`SET lock_configuration=false`、`SET allowed_paths=…`，都报 "locked"。此外 `CREATE TABLE` 因只读失败，`COPY … TO`（包括覆盖本库 Parquet）和 `read_csv('/etc/passwd')` 报 PermissionException。
   - 超时用 `threading.Timer` 调 `con.interrupt()`，报 `QueryTimeout`。行数上限用 `.limit(max_rows+1)`，超过报 `TooManyRows`。PermissionException 报 `QueryRejected`，其他 DuckDB 错误报 `QueryError`（语法错误不算 Rejected）。
3. **出处**：结果列里有 `obs_id` 时，回查这些 obs_id，按首次出现去重，还原成 `Observation`。所有字段都能原样还原，测试覆盖了 dims、时区、空字段和 38 位精度的数值。没有 `obs_id` 时 `provenance=[]`，并在 warnings 里提示保留 obs_id。
4. **describe()**：按 (source_id, table_key) 列出 dataset_keys、surveys、indicators、area_count（不含空 area_code）、time_start/time_end 和行数。
5. **空值与注记**：`-`、`…`、`x`、`秘匿` 等注记的 value 是 NULL，note 原样保存，不会被当成 0（`sum` 得 NULL，`count(value)` 不计它们）；真正的 0 仍是 0，note 为空。

## 假设与偏离
- **结果取回走 Arrow**：DuckDB 把 TIMESTAMPTZ 直接转成 Python 对象需要 pytz，环境里没有；Arrow 用 zoneinfo。`fetched_at` 按连接的会话时区返回（本机是 Asia/Tokyo），时刻本身不变。
- **结果里的 Decimal 去掉了多余的尾零**（`13960000.0000000000` → `13960000`），数值不变。
- 能无损存进 `DECIMAL(38,10)` 的值（小数不超过 10 位、整数部分不超过 28 位）才接受，否则 `write_table` 报 ValueError，不做舍入。
- 契约没有保证 obs_id 跨表唯一。不同表撞了 obs_id 时，出处取 (source_id, table_key) 最小的那张表。
- 没有另写 `with_provenance` 包装：要求 4 里两种做法二选一，这里选了“查询保留 obs_id”，缺 obs_id 时给提示。
- 白名单按名字拒绝 `query` 等函数名，所以把列别名起成 `query` 也会被拒，属于宁严勿宽。
- 测试调用了私有方法 `_connect()`，用来直接验证引擎层的锁定。

## 没做 / 遗留
- PLAN 4.3 的“不同调查不能相加、不能算增长率”没有在这里拦截或提示（任务要求里没有）。建议放在上层的 `query_sql` 工具里，可以看 `provenance` 里有几种 survey。
- 查询连接没设内存上限，只有超时和行数上限兜底。
- 并发：同一张表被多个进程同时写时，以最后一次为准。查询先列文件再读，读之前文件恰好被删的话会报 QueryError。

## 风险 / 没核对
- 没跑 ruff（`ruff check` 不在放行命令里），行宽是人工检查的。
- 白名单的函数名单是对照 DuckDB 1.5.6 和常见扩展写的。今后升级 DuckDB 时，新出现的读文件函数要靠引擎层（外部访问关闭、配置锁定、只放行本库文件）兜底。
