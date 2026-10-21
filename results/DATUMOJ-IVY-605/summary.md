# DATUMOJ-IVY-605 完成说明

## 修改

- `parse_pending` 对同一快照、解析器和版本的 `failed` 记录重新调用解析器；`ok` 和 `no_parser` 仍不重新解析。
- 成功重试通过 `Manifest.record_parse` 覆盖失败记录，测试确认 `get_parse` 返回 `ok` 且清除了错误。
- `ParseSummary` 保留 `parsed` 作为本次所有成功项，新增 `newly_parsed` 和 `retried` 供分别统计；`failed` 列出本次仍失败的快照 ID 和错误原因。现有 CLI 在失败映射非空时打印失败映射并以退出码 1 退出。
- 为兼容受保护的旧测试，`skipped` 仍统计本次遇到已有解析记录的快照，包括实际重新尝试的失败记录；重试状态通过 `retried` 和 `failed` 单独体现。

## 要求核对

1. `failed` 会重试；`ok`、`no_parser` 继续跳过。
2. 同版本重试成功覆盖 manifest 状态为 `ok`，且错误清空。
3. 汇总字段区分首次成功 `newly_parsed`、重试成功 `retried` 和仍失败 `failed`；失败映射包含快照及原因。parse 命令对非空失败映射维持非零退出。
4. 受保护的 `tests/test_pipeline.py` 全部通过。
5. 新增假解析器测试覆盖先失败后同版本修复、连续失败两次均报告、成功快照再次运行不解析。

## 验收输出

- `make check T=tests/test_pipeline_retry.py`：3 passed，完整输出见 [test_pipeline_retry.log](test_pipeline_retry.log)。
- `make check T=tests/test_pipeline.py`：9 passed，完整输出见 [test_pipeline.log](test_pipeline.log)。
- 沙箱中 `uv` 不可用；`make check` 按项目设置使用既有主仓库只读虚拟环境完成验收。

## 变更文件

- `src/datumoj/pipeline.py`
- `tests/test_pipeline_retry.py`
- `results/DATUMOJ-IVY-605/summary.md`
- `results/DATUMOJ-IVY-605/test_pipeline_retry.log`
- `results/DATUMOJ-IVY-605/test_pipeline.log`

未改契约、解析器、索引、CLI、测试夹具或依赖文件；没有提交 git commit。

跳过了整个仓库测试套件；没有验证 CLI 的终端文案集成。现有 CLI 只输出总成功数，因此新增的首次解析/重试成功拆分目前通过 `ParseSummary` 提供给调用方，CLI 文案未拆分显示；`skipped` 为兼容受保护测试而沿用已有记录数口径，包含会被重试的失败记录。
