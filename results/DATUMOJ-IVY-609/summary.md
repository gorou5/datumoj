# DATUMOJ-IVY-609 完成说明

## 改动

- 每个成功解析的快照都会按结果提升目录状态：有观测值为 `observations_loaded`，其他结果（包括空结果）为 `content_fetched`。只对目录中匹配 URL 的资源写状态，并根据现有状态取最大值。
- 目录资源 upsert 后用 `Manifest.last_success(resource.url)` 补记此前已成功抓取的资源；资源快照早于目录页解析时也能补上。
- 完整 `reindex` 按来源（未指定来源时扫描全部）核对目录资源的成功快照并补状态，不重解析快照；`vectors_only` 仍只重试向量。
- 新测试使用真实 `SqliteCatalogStore`、`SqliteManifest`、`FileRawStore` 和 CKAN 页面及资源 fixture，覆盖两种解析顺序、失败抓取、状态不降级、重复解析和 reindex 补记。

## 验收

任务列出的四条 `make check` 命令均通过，合计 21 项测试。完整输出见 [acceptance.log](acceptance.log)，单项日志也在本目录。沙箱提示 `uv` 不可用，`make check` 按项目规则切换到主仓库 `.venv` 执行。

`git diff --check` 通过。改动文件清单见 [changed-files.txt](changed-files.txt)。

## 复核

- 空解析结果仍提升为 `content_fetched`；解析出观测值仍为 `observations_loaded`。
- 资源先于目录页解析时，由目录 upsert 后的成功快照回查补记；目录先解析时，由资源快照解析后补记。
- `observations_loaded` 不会被 `content_fetched` 覆盖；没有成功快照的资源保持 `catalog_only`。
- `reindex` 不触发快照解析，只按 manifest 成功记录提升目录资源状态。
- 使用真实 SQLite 目录库验证；原有流水线、重试和向量测试均通过；修改只在任务 scope 内。

跳过了网络访问和完整仓库测试；没有核对真实采集数据库中的 3,187 条旧记录。剩余风险：目录查询使用契约提供的结果上限 1,000,000 条，超过该数量的目录条目不会被单次 reindex 扫描。
