# DATUMOJ-IVY-602 完成说明

## 完成内容

- 新增 Typer 命令 `crawl`、`parse`、`index`、`search`、`sql`、`status`，并支持 `python -m datumoj` 启动。
- 新增 `wiring.build(data_dir, overrides=None)`：组装本地存储、流水线和分析工具；依赖可由 overrides 注入。缺少所需实现时命令以 1 退出并提示“该功能尚未接入”。向量实现可选，缺失时继续使用关键词检索。
- `search` 人读输出包括标题路径、locator、摘要和完整出处；零命中显示状态与当前来源。`search` / `sql` 支持 JSON。
- 区分参数错误退出码 2 与运行失败退出码 1；测试通过 CliRunner 和内存假实现覆盖全部命令、dry-run、零命中、JSON 及缺失功能提示。

## 验收

运行 `make check T=tests/test_cli.py`：4 passed。沙箱中的 `uv` 不可用，Makefile 按既有逻辑使用主仓库只读 `.venv`；测试通过。测试的抓取请求由 `FakeHttpFetcher` 回答，没有访问网络。

## 变更文件

- `src/datumoj/cli.py`
- `src/datumoj/__main__.py`
- `src/datumoj/wiring.py`
- `tests/test_cli.py`
- `results/DATUMOJ-IVY-602/test.log`
- `results/DATUMOJ-IVY-602/summary.md`

本任务不实现任何协议，因此没有对应的协议合格测试套件；假实现用于验证命令调用路径。

跳过：未运行全仓测试，也未调用真实 HTTP 或向量模型。风险：可选向量依赖缺失时检索退化为关键词；具体实现导入缺失时对应命令会提示尚未接入。
