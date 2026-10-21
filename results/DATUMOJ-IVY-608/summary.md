# DATUMOJ-IVY-608 完成说明

已修复 MCP 工具调用与 SQLite 连接创建线程不一致的问题。

## 实现与要求核对

1. `create_server` 同时接收 `Callable[[], AnalysisTools]` 和原有 `AnalysisTools` 实例。工厂延迟到首次工具调用时运行，并在该服务器运行期间缓存返回的工具层。
2. 服务器 lifespan 创建一个 `ThreadPoolExecutor(max_workers=1)`。七个工具均通过异步包装提交到这个执行器；工厂和同步工具体在同一个专用线程执行，调用串行排队，事件循环异步等待。退出时在专用线程释放缓存实例并关闭执行器。模块说明明确：直接传入实例仍受其存储的线程约束，不能迁移已创建的 SQLite 连接，应使用工厂构造线程绑定存储。
3. `main()` 把环境变量解析出的工厂直接交给 `create_server`，不在主线程调用工厂；新增测试断言这一点。
4. 原有 `_guarded` 保持不变，仍在同步工具体外捕获异常并转换成 `ToolError`。工厂异常也经过同一处理；测试验证错误返回后会话继续响应。
5. protected 的 `tests/test_mcp_server.py` 未修改，全部 15 个测试通过，覆盖直接传入实例、七个工具、参数错误及提示词等原有行为。
6. 新增 6 个离线测试：工厂在 `tmp_path` 构建真实 `SqliteChunkStore`、`SqliteKeywordIndex`、`SqliteManifest` 和 `FileRawStore`；经 MCP 内存会话检索并引用，核对快照哈希和还原的原文；并发调用按提交顺序串行成功，线程被阻塞时事件循环仍可推进；工具和工厂异常均返回 MCP 错误结果。测试同时核对创建与调用线程一致、工厂只成功构造一次、会话结束后线程退出及存储在所属线程关闭。

未引入其他业务实现依赖：解析器、注册表、目录和数值存储使用 `datumoj.testing` 假实现；检索适配器沿用 `FakeRetriever`，同时实际查询真实 SQLite 关键词索引并断言命中一致，块和出处均读真实 SQLite。没有修改存储、工具层、契约、受保护测试或夹具，没有新增依赖，没有提交 git commit。

## 验收与证据

两条命令均按任务原文亲自运行，退出码均为 0：

| 命令 | 结果 | 输出 |
| --- | --- | --- |
| `make check T=tests/test_mcp_server_threads.py` | 6 passed in 0.96s | [线程回归日志](test_mcp_server_threads.log) |
| `make check T=tests/test_mcp_server.py` | 15 passed in 1.02s | [原有测试日志](test_mcp_server.log) |

`make check` 按仓库规则自动使用已有主仓库虚拟环境，未安装依赖或联网。`git diff --check` 通过。完整变更文件清单见 [changed_files.txt](changed_files.txt)，所有文件均在任务 scope 内。已核对全部 `create_server` 调用方：生产入口为本模块 `main()`，原有测试直接传入实例的调用保持兼容。

跳过/未核对：未运行全仓库测试、外部宿主 stdio 会话或独立 other_family 评审；本次按要求验证 MCP 内存会话与两条自动验收。
风险/限制：直接传入主线程已打开 SQLite 连接的实例仍不满足线程约束，真实存储接线必须传入在专用线程构造全部存储的工厂；没有为 SQLite 放宽线程检查。
