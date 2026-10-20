# DATUMOJ-IVY-502 完成说明（第 2 次尝试）

## 变更文件
- `src/datumoj/index/embed.py`：`HashEmbedder`、`OllamaEmbedder`（`/api/embed`，批量、超时、对网络错误/超时/5xx 退避重试，4xx 直接失败，校验向量数量与维度）
- `src/datumoj/index/vector.py`：`LanceVectorIndex`（LanceDB，余弦；按 chunk_id + 文本 SHA-256 只嵌入新增或变更块；每批落盘，失败重跑跳过已完成块；目录内 `_meta.json` 记录模型名和维度，不符抛 `ModelMismatch`）
- `tests/index/test_vector.py`

## 针对上次打回
上次的实现不在本 worktree 中，所以整体重写。补了超时测试：本地假 Ollama 可按脚本延迟响应，
`test_ollama_timeout_is_retried`（首次超时、重试成功，共 2 次请求）和
`test_ollama_timeout_fails_after_retries`（始终超时 → `EmbeddingError`）。去掉 `timeout` 设置这两条会失败。

## 验证
`make check T=tests/index/test_vector.py`：27 passed。通过 `EmbedderSuite`（HashEmbedder 与走假 Ollama 的 OllamaEmbedder）和 `VectorIndexSuite`。测试全部离线，只用 HashEmbedder 和本地假服务。

## 跳过 / 风险
- 没有核对真实 Ollama/bge-m3（留给 H5）。
- `table_names()` 在当前 lancedb 有弃用警告，功能正常；没有改用 `list_tables()`，因为不确定 `lancedb>=0.13` 下的返回类型。
