# DATUMOJ-IVY-607 完成说明

## 改动

- 解析先保存文档块和关键词索引；向量删除或写入抛错时，解析仍记为成功，并在 `ParseSummary.vectors_missing` 和 CLI 输出中列出快照与原因。
- 在数据目录的 `vectors-state.json` 按文档持久化暂缺块数、快照 ID 和错误。`datumoj index --vectors-only` 只重试已记录的暂缺文档；成功后清除对应暂缺状态。`--rebuild` 也会重试向量，向量失败不会中断关键词重建。
- `DATUMOJ_VECTORS=off` 时优先于注入覆盖项生效，不导入或实例化向量模块；检索器收到 `vectors=None`，走关键词路径。
- `datumoj status` 无论是否登记来源都会显示向量状态：关闭、可用块数，或暂缺块数与最近错误。

## 评审核对

- 嵌入失败后，文档与关键词索引仍写入，manifest 解析状态为 `ok`；新测试验证暂缺快照与错误原因。
- 暂缺状态写入数据目录 JSON，新的依赖实例可读取；`--vectors-only` 补做成功后状态清空，测试验证向量检索可用。
- 新测试设置 `DATUMOJ_VECTORS=off`，并断言向量模块未导入、`wiring.vectors` 与流水线向量依赖均为 `None`。
- 原有流水线、重试和 CLI 验收均通过；变更仅限任务 scope 内文件。

## 验收

见 [acceptance.log](acceptance.log)，四条任务验收命令全部通过。沙箱中 `uv` 不可用，`make check` 按仓库规则使用已有主仓库 `.venv` 执行。

变更文件清单见 [changed-files.txt](changed-files.txt)。

跳过了任务未要求的全仓测试和 lint；未连接现场 Ollama/LanceDB（测试保持离线）。状态文件无法写入时，当前进程会保留暂缺状态并将写入错误附加到最近错误；进程重启后的持久化仍取决于数据目录可写。
