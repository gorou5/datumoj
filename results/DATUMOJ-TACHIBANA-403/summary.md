# DATUMOJ-TACHIBANA-403 完成说明

## 修改内容

- 新增 `src/datumoj/core/registry_export.py`，复用 `core.registry.load_registry` 校验 YAML，按 schema 映射字段并生成排序、缩进 2、UTF-8、末尾换行的确定性 JSON。
- 在 `src/datumoj/cli.py` 增加 `datumoj registry export [--out catalog]`。
- 提交六份 `catalog/*.json`，并在 `docs/interoperability.md` 说明内部模型与团队标准的字段映射。
- 在 `tests/core/test_registry_export.py` 覆盖访问方式、许可、认证、未知值、格式稳定性、schema、仓库导出同步和 CLI。

## 方案

通过已有注册表加载器复用字段校验，不新增依赖或修改来源 YAML。未知 `kind` 映射为 schema 允许的 `other`；许可 URL 缺失时省略可选 URL 字段，避免输出不符合 URI 格式的占位值。未使用自建 YAML 解析或扩展 schema。

## 验收与检查

- `make check T=tests/core/test_registry_export.py`：15 passed。
- `make check T=tests/test_cli.py`：4 passed。
- `tests/validate_schemas.py`：全部通过；六份 catalog 均通过 data-source schema 校验。
- 新导出与已提交 catalog 文件名及内容逐字节一致；`git diff --check` 通过。
- `make check` 因 uv 缓存不可用，按 Makefile 约定改用主仓库 `.venv` 执行成功。没有安装依赖。

## 复核与风险

本任务不实现 `contracts.py` 中的协议，因此对应的契约合格测试不适用；导出测试只使用本地文件，没有联网。没有改动 scope 外文件、schema 或 `sources/*.yaml`。尚未执行 other-family 复核和 human sampled；尝试启动只读复核 agent 时，调度工具两次因 thread store 缺少 rollout 而失败。发布者及许可映射只反映 YAML 中现有说明，不构成对来源机构或许可事实的独立核实。

跳过了 other-family 复核及 human sampled；未独立核实 YAML 中的机构和许可事实。若将来删除来源，也要删除对应已提交 catalog 文件，当前同步测试会报告遗留文件。
