# DATUMOJ-TACHIBANA-601 完成说明

已实现解析后的变化检测与 SQLite 变化日志。六条指定验收命令全部通过，共 81 项测试；没有 git commit。

## 修改文件与方案

- `src/datumoj/store/changes.py`：新增 `SqliteChangeLog`，事务批量追加；按契约 event_id 去重并保留首条，支持时间、来源、事件类型联合过滤和稳定排序，时间统一转 UTC 比较。
- `src/datumoj/changes.py`：新增纯函数 `detect`、正文规范化与可调常量 `SIMILARITY_THRESHOLD = 0.98`。法令优先比较修订 ID，缺失时比较正文哈希；统计表比较快照哈希；网页/PDF 使用 difflib 相似度，等于 0.98 不算修改。摘要为单行文字。
- `src/datumoj/pipeline.py`：注入可选 `change_log`；覆盖文档前读取旧正文，写入文档后检测并追加事件；已存储快照版本不重复检测，避免重试或解析器升级产生反向修改。解析失败记 `parse_failed`；每个 URL 的最新抓取失败记 `fetch_failed`；曾成功而最新返回 404/410 时为关联文档记 `removed`，保留原文档供引用。检测、旧版本读取和日志写入异常仅记 warning。
- `src/datumoj/wiring.py`：在存储配置的 data_dir 下接入 `changes.sqlite`，支持 `overrides["change_log"]`，传给 PipelineDeps。
- `tests/store/test_changes.py`：继承并运行 `ChangeLogSuite`（内存库、文件库），补充重开持久性、跨时区排序、批次回滚测试。
- `tests/test_change_detection.py`：覆盖全部五种事件、0.97/0.98/0.99 边界、可调阈值、导航/日期噪声、业务日期、法令修订与无修订回退、统计表、重复解析及升级、失败分类、404/410、无块文档、同哈希不同资源、来源过滤、异常隔离、接线与覆盖。
- `results/DATUMOJ-TACHIBANA-601/`：完成说明、六份测试日志、范围核对日志、完整变更文件清单。

沿用契约的 `ChangeEvent.make` 生成稳定 ID，没有新增 ID 算法或依赖。内部模块直接导入，不需改包导出、夹具或配置文件。没有采用整份网页原始字节比较，以正文规范化和阈值过滤导航、日期噪声。IVY 法令解析器尚未设置 kind，已兼容其 `law_num` 元数据及明确的法令修订元数据。

四参数 `detect` 使用确定的 epoch 时间占位以保持纯函数；独立调用者可传 `detected_at`，流水线始终传入实际检测时间，不把检测时间当作数据发布时间。

## 要求逐项核对

| 要求 | 实现和验证 |
| --- | --- |
| 1. SQLite ChangeLog、存储位置、overrides、契约套件 | 文件/内存库均通过 ChangeLogSuite；接线测试检查路径与覆盖实例 |
| 2. added、法令、网页/PDF 阈值、统计表与摘要 | 单元测试逐项覆盖，业务日期保留，摘要折为单行 |
| 3. 写入后检测，出错仅 warning | 检测、append、旧版本读取和 fetch 日志读取故障测试均保留解析结果 |
| 4. parse_failed、fetch_failed 分类、404/410 removed | 失败解析重试、抓取分类、最新状态、从未成功不删除、同哈希不同资源等测试 |
| 5. 同一快照幂等 | 契约 ID 和 SQLite 去重；重复运行及解析器升级事件列表不变 |
| 6. 新测试及流水线回归 | 下表六条验收命令全部通过；现有四个 test_pipeline*.py 文件均已运行 |

## 验收证据

| 原文命令 | 结果 | 日志 |
| --- | --- | --- |
| `make check T=tests/store/test_changes.py` | 17 passed | `tests_store_test_changes.log` |
| `make check T=tests/test_change_detection.py` | 43 passed | `tests_test_change_detection.log` |
| `make check T=tests/test_pipeline.py` | 9 passed | `tests_test_pipeline.log` |
| `make check T=tests/test_pipeline_retry.py` | 3 passed | `tests_test_pipeline_retry.log` |
| `make check T=tests/test_pipeline_vectors.py` | 4 passed | `tests_test_pipeline_vectors.log` |
| `make check T=tests/test_pipeline_content_state.py` | 5 passed | `tests_test_pipeline_content_state.log` |

uv 在沙箱内不可用，以上命令均按项目 Makefile 自动使用主仓库 .venv 的 Python，以当前 worktree 的 src 为 PYTHONPATH。没有安装、更新依赖或修改锁文件。测试全部使用契约假实现、人工字符串和 tmp_path；没有 HTTP/S3 请求，也没有写入受跟踪的数据路径。

## 范围、风险与未验证项

变更仅位于任务 scope；契约、共享假实现、受保护的流水线测试、夹具、依赖与构建配置均未修改。新代码只新增对契约、任务内模块及既有依赖的导入，未引用其他 TACHIBANA 实现。任务包没有“评审中的高置信度质疑”条目。无需人工确认或扩大权限，跨模型评审与人工抽查仍由调度器安排。

导航与日期过滤是保守的明确行规则，依赖解析器先抽取正文，不进行语义上的导航识别。按要求，日志故障不阻塞解析，因此故障期间的文档事件可能缺失，不自动回溯补写。契约 ID 不含失败快照 ID 或检测时间：同一资源、事件种类和旧/新哈希相同的抓取失败会合并，保留首条摘要中的失败 snapshot_id；成功事件以哈希还原快照，失败事件以 URL 和摘要中的 snapshot_id 回查 manifest。

未做报告/MCP、IVY 历史回溯、真实联网、生产数据验证或全仓库测试；没有改变许可或外部服务权限。剩余风险为启发式正文过滤和日志故障时的事件缺失。
