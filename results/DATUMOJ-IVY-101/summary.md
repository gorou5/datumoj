# DATUMOJ-IVY-101 完成说明：raw 存储按内容寻址

## 实现

- 新增 `FileRawStore`，实现 `RawStore` 契约的 `put`、`get`、`open`、`exists`、`verify`、`path_for`。
- 根目录依次取显式参数、`DATUMOJ_DATA_DIR`、默认 `./data`。
- `put` 以 1 MiB 分块读取流并计算 SHA-256，先写同文件系统临时文件，再原子替换到 `raw/<前两位>/<sha256>`；成功文件权限设为只读。目标已存在时保留原文件。
- SHA 参数按契约校验；缺失文件抛 `NotFoundError`，哈希不匹配抛 `CorruptedBlob`。

## 测试与验收

- `TestFileRawStore` 继承 `RawStoreSuite`，覆盖契约合格测试；另测了超过 10 MiB 的分块流、三种根目录来源、只读权限及重复写入不覆盖已有内容。
- 验收命令：`make check T=tests/core/test_rawstore.py`
- 结果：`18 passed in 0.22s`。沙箱中 `uv` 不可用，`make check` 按提示使用现有 `.venv`。
- 测试不联网，只导入本任务实现、契约和 `datumoj.testing` 合格测试。

## 范围核对

- 变更仅在任务 scope：实现、对应测试及本目录交付证据。
- 调用方检索发现暂无真实 `RawStore` 实现的其他调用方；未修改契约、假实现、夹具、配置或导出。
- 任务未提供“评审中的高置信度质疑”一节。

## 未做与风险

- 未运行全量测试或 lint；验收只运行任务指定的测试文件。
- 并发写入同一摘要时可能发生一次冗余原子替换，但两次写入的摘要和字节相同；已有目标在通常重复写入路径中不会被覆盖。
