# DATUMOJ-IVY-402 完成说明

## 验收

执行 `make check T=tests/store/test_catalog.py`：

```text
make check: uv 不可用（可能在沙箱里），改用 /Users/aaron/workspace/datumoj/.venv/bin/python
...................                                                      [100%]
19 passed in 0.19s
```

使用仓库 Makefile 的只读虚拟环境回退，未安装依赖或访问网络。

## 变更文件

- `src/datumoj/parsers/ckan_meta.py`：实现契约 `Parser`，把 package JSON 转成 `Dataset` 和 `Resource`；保留数据集级/资源级许可、格式、大小等源值，把组织、地域、更新频率放入目录字段；无时区的 CKAN 修改时间按 UTC 解析。
- `src/datumoj/store/catalog.py`：新增 SQLite `CatalogStore`，使用 FTS5 trigram 索引标题、说明和标签；实现原值往返、upsert 状态保留、资源状态更新、来源/组织/格式/更新时间筛选与 `CatalogHit` 状态汇总。短于三个字符的查询使用归一化文本匹配，以满足 FTS5 trigram 的长度限制。
- `tests/store/test_catalog.py`：继承并运行 `CatalogStoreSuite`、`ParserSuite`；覆盖离线 CKAN 样本、元数据映射、许可缺失与两级差异、原始格式/许可值、过滤、短查询、正文状态、原子失败和重开数据库。
- `results/DATUMOJ-IVY-402/summary.md`：本完成说明及验收记录。

## 要求核对

1. CKAN package JSON 转换为契约模型；标题、说明、标签、组织、地域、更新频率、数据集/资源许可、资源格式和 URL 均有覆盖。
2. SQLite `CatalogStore` 实现 `upsert`、`get_dataset`、`resources_for` 和 `set_content_state`，正文状态使用契约 `ContentState`。
3. `search(query, filters, k)` 检索标题、说明和标签；支持契约中的来源、组织、格式、更新时间过滤，并返回正文抓取/观测值状态。
4. `CatalogStoreSuite` 与 `ParserSuite` 均通过；新增测试覆盖转换、搜索、过滤和许可原值。
5. 格式与许可规范值单独存储，空白格式/许可归一为“未知”；读取仍还原原值。CKAN 的登记 `size` 保留为参考字段，不用于正文状态或实际下载大小。
6. 实现只依赖 `datumoj.core.contracts`、`datumoj.core.models` 和标准库；测试使用 `datumoj.testing` 契约套件/样本。测试数据来自只读本地 fixture，没有联网测试。

## 未覆盖项与风险

- 未运行全仓测试或 lint；任务要求的验收命令已通过。
- 契约没有许可过滤条件，因此只保存规范化许可值，不提供许可检索过滤。
- 未单独测试 SQLite 并发写入和损坏数据库恢复；当前契约合格测试及本任务验收未要求这些场景。
