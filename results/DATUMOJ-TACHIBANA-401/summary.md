# DATUMOJ-TACHIBANA-401 完成说明

## 修改内容

- `src/datumoj/crawlers/ckan.py`：增加可选 `scope.datastore`。仅为 `datastore_active: true` 且具有资源 ID 的资源创建 `datastore_search` 页任务；按 `_id asc` 请求，元数据保存 `role=datastore`、`resource_url`、`dataset_id` 和数据集级许可。偏移按实际返回记录数推进，空页停止且忽略 `_links.next`；达到 `scope.datastore_max_rows` 时停止并写 warning。短页在响应有可信 `total` 且尚未到 total 时继续，没有可信 total 的短页作为末页。DataStore 条件请求返回 304 时复用原始快照正文以继续分页。
- `sources/jp-bodik.yaml`：新增 BODIK CKAN 来源配置，设置实测 action API、0.5 请求/秒、daily 频率、200MB 单文件与 10GB 总容量、`unverified` / `internal_only` 许可，并为该来源打开 DataStore 分页。仓库计划规定新来源在调度中默认关闭，需显式运行 `datumoj schedule enable`；`schedule: daily` 仅表示启用后的频率。
- `tests/crawlers/test_ckan_datastore.py`：用 `pytest-httpserver` 在本地回放 BODIK 样本并覆盖目录分页、67 条 DataStore 记录拼接、短页续读、空页不跟随 `_links.next`、行数封顶和 warning、数据集级许可快照 meta、默认 limit/max rows，以及 304 页面复用。
- `results/DATUMOJ-TACHIBANA-401/acceptance.log`、`changed-files.txt`：记录验收输出和本次文件清单。

## 实现与测试

DataStore 结果仍只按原始页保存快照，不转换观测值，也不修改目录入库状态。通用 CKAN 的 `datastore` 默认是 `false`，因此 e-Gov 配置和原有采集行为不变。实现只依赖契约、本任务内 CKAN 模块及标准库；没有修改契约或新增依赖。原有 `CkanCrawler` 合格测试随 crawler 全套测试运行。

验收命令按任务原文执行，均通过：

- `make check T=tests/crawlers/test_ckan_datastore.py`：8 passed。
- `make check T=tests/crawlers`：181 passed。
- `git diff --check`：通过。

沙箱内 `uv` 无法初始化其缓存，因此 `make check` 按 Makefile 预设流程改用主仓库只读 `.venv`；测试成功。HTTP 测试只连接本地 `pytest-httpserver`，未访问网络。录制样本只读；未录制的 DataStore 中间页由测试从样本记录构造，以覆盖完整的 67 条分页序列。

## 范围、限制与风险

本次变更仅涉及任务 scope 中的 CKAN 采集器、BODIK 来源配置、测试和结果记录；没有改动受保护文件或其他任务模块。任务没有单列“评审中的高置信度质疑”，不适用。许可尚未逐项复核，来源保持 `unverified` / `internal_only`，资源级许可 ID 保留在快照 meta；DataStore 记录仍是原始快照。

跳过了真实 BODIK 抓取和逐个自治体许可核验；没有验证 DataStore 中间页之外的所有门户变体。未发现需要人类确认的实现阻塞；来源仍需按仓库调度流程显式启用。主要风险是 DataStore `total` 若估算不准，页终止位置会受源端 total 影响；行数上限按请求页大小执行，并在达到上限时记录 warning。
