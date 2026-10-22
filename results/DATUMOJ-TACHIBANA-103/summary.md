# DATUMOJ-TACHIBANA-103 完成说明

实现完成，三条自动验收全部通过，待任务要求的人类评审。未 git commit。

## 修改文件与方案

- `src/datumoj/core/storage.py`：统一 Pydantic 配置；本机目录参数优先于环境变量，默认 `./data`、local raw；校验 S3 必填项与 endpoint，不接收凭证字段，错误文本隐藏输入。
- `src/datumoj/core/rawstore_s3.py`：使用已锁定 boto3，实现 RawStore 五个方法。对象键为 `<prefix>/raw/<sha前两位>/<sha>`，空前缀不加前导斜线；首尾斜线规范化。
- `src/datumoj/core/rawstore.py`、`src/datumoj/analysis/tools.py`、`src/datumoj/cli.py`：经统一配置取得本机目录，保留原有本机实现及命令参数。
- `src/datumoj/wiring.py`：按 raw_backend 选择实现，保留 overrides；其余库、向量、状态文件仍按本机 data_dir 组装；MCP build_tools 保留向 build 传字符串的兼容性。
- `tests/core/test_storage.py`：默认值、环境变量、目录参数优先、endpoint 回退、缺项、无凭证配置及各入口接线测试。
- `tests/core/test_rawstore_s3.py`：继承完整 RawStoreSuite；仅连接 ThreadedMotoServer 的回环 HTTP endpoint；增加键布局、实际跳过重复上传、20MB 流式输入、损坏读取、只读可定位流、错误凭证脱敏和 S3 接线测试。
- `docs/adr/0002-network-storage.md`：状态为“提议”，比较 D1、本机一致性备份至 R2、其他数据库/服务方案；说明远端 LanceDB 与 Parquet/DuckDB 的可行性、现有代码边界和代价。
- 本目录：测试原始输出、变更清单、本说明和机器可读的适配器描述 `module.yaml`。

沿用现有协议和 boto3，不新增依赖、契约、包装服务或迁移机制。上传先按 1MiB 块计算 SHA256 并落临时文件；HEAD 存在则不上传，否则由 SDK 流式/分片上传。下载按块写临时文件，校验完成才返回只读流；get 返回全部 bytes，verify 每次重新下载校验。正常和异常路径均释放临时文件，open 成功后由调用方关闭。

凭证只在构造 S3 客户端时从 R2_ACCESS_KEY_ID、R2_SECRET_ACCESS_KEY 环境变量取得，显式传入 SDK，不使用默认凭证链查找其他凭证。SDK 操作错误转换为不含远端响应的静态 ContractError，隐藏原异常链；404 / NoSuchKey 对应不存在，其余权限/服务错误不会冒充不存在。错误凭证测试通过 Stubber 注入带有虚构凭证回显的鉴权失败，因为 moto 默认接受任意签名；数据与完整协议测试仍通过 moto HTTP 完成。

放弃本阶段迁移 SQLite/D1、把在线数据库文件挂载在对象存储，以及整块缓冲大文件的方案；前两项超出本任务，后一项不满足流式要求。

## 逐项验收

| 要求 | 核对结果 |
| --- | --- |
| 1 统一配置与优先级 | 已实现；包含全部规定字段，另支持 DATUMOJ_S3_REGION，默认 auto；R2_ENDPOINT 优先于 DATUMOJ_S3_ENDPOINT。 |
| 2 四处目录读取收拢、默认不变 | src/datumoj 内只有 storage.py 直接读取 DATUMOJ_DATA_DIR；CLI 其余出现处仅为帮助文本。默认仍为本机 data 目录。 |
| 3 S3 键、流式、去重、校验与错误 | 已实现；get/open 拒绝损坏对象，verify 重新校验，exists 用 HEAD。 |
| 4 完整 RawStoreSuite / moto endpoint | 通过；包括空对象、流、缺失、非法 SHA256、损坏与协议符合性。 |
| 5 接线与其他库保持本机 | 已实现并测试；全套原有 CLI、MCP、调度及集成测试通过。 |
| 6 凭证安全 | 配置无凭证字段；缺凭证显示变量名；HEAD/GET/PUT 错误的 repr、格式化 traceback、捕获日志均不含注入的虚构凭证。 |
| 7 网络存储 ADR | 已完成，仅提议；近期建议本机运行和一致性备份，后续再评估迁移。 |
| 8 新测试与全量回归 | 三条指定命令全部通过，见下表。 |

任务材料未包含“评审中的高置信度质疑”条目。未改测试夹具、既有测试、契约、依赖/锁文件、Makefile、.env.example 或任何 scope 外文件。

## 自动验收证据

| 原文命令 | 最终结果 | 原始输出 |
| --- | --- | --- |
| `make check T=tests/core/test_storage.py` | 13 passed in 0.30s | storage.log |
| `make check T=tests/core/test_rawstore_s3.py` | 26 passed in 2.31s | rawstore-s3.log |
| `make check T=tests` | 1794 passed, 37 warnings in 51.65s | tests.log |

三条命令均亲自执行，退出码均为 0。make 按仓库既有逻辑，在 uv 缓存不可用时使用主仓库只读 .venv，未安装或改动依赖。全量警告来自 multiprocessing fork 与 LanceDB table_names 弃用提示。另已运行 git diff --check，通过。

首轮全量测试为 1793 passed、1 failed：build_tools 向 build 传入 Path，而原有集成测试要求字符串。已修复实现并重跑以上全部验收；未修改该既有集成测试。

## 限制与人类待定事项

- 未连接真实 R2、D1 或任何生产资源，未部署、迁移或实际备份；真实云端权限、性能、配额和远端 LanceDB/DuckDB 兼容性尚未验证。
- 流式暂存仍需要与对象体积相当的临时磁盘空间；open 在完整校验后才返回，具有整对象下载延迟。并发相同内容可能在各自 HEAD 后重复传输相同字节，未引入分布式锁。
- 本任务不改变数据许可；模块描述标记未知许可。凭证错误测试使用虚构值，未读取或核验真实账号凭证。
- 人类需评审本任务、决定是否采纳 ADR，并另定备份保留期、恢复目标和后续迁移范围；本交付未代替人类批准。

跳过：真实云端连接、资源创建、数据库迁移与部署（均按任务排除）。剩余风险：真实服务兼容性待验，临时磁盘容量与完整下载延迟需在部署前评估。
