# DATUMOJ-IVY-104 完成说明：来源注册表 schema 与校验

## 验收

- `make check T=tests/core/test_registry.py` → `13 passed in 0.23s`
- 沙箱中的 `uv` 不可用，Makefile 按项目约定改用 `/Users/aaron/workspace/datumoj/.venv/bin/python`；测试全程离线。
- `TestRegistryConformance` 继承并运行了 `RegistrySuite`。

## 对照要求

1. `registry.py` 复用 `datumoj.core.contracts.SourceSpec` / `Registry` 和 `datumoj.core.models.Redistribution`，校验来源字段并实现 `get()`、按 ID 排序的 `all()`；没有另定义契约模型。
2. `load_registry(dir)` 只读取目录中的 `*.yaml`，校验 ID 与文件名一致及 ID 唯一；schema 错误包含文件名和字段。
3. 递归检查字段名含 `key`、`token` 或 `secret` 的配置值，只接受 `env:` 引用；报错不回显值。`needs_key` 的具体变量名格式继续由契约校验。
4. 许可复用嵌套 `license: LicenseInfo`。非 `allowed` 的再分发仅允许 `internal_only`；标记 `allowed` 时必须提供许可名称、URL、署名和核对日期。
5. 新增 `sources/jp-egov-law.yaml`，配置端点白名单、1 次/秒、文件数及累计字节上限；许可仍为 `unverified` / `internal_only`。`sources/README.md` 说明字段、示例和逐来源核对流程，并提醒不能把公共数据条款套用于所有日本来源。
6. 测试覆盖合法加载、实际 e-Gov YAML 加载、ID 不符、重复 ID、缺字段、明文凭据、空 scope、未核对却放开再分发；另运行注册表契约合格测试。
7. 使用契约的 `SourceSpec` 和嵌套 `LicenseInfo`，未改 `models.py`、`contracts.py` 或其他开发线实现。

## 变更文件

- `src/datumoj/core/registry.py`
- `sources/jp-egov-law.yaml`
- `sources/README.md`
- `tests/core/test_registry.py`
- `results/DATUMOJ-IVY-104/summary.md`

未新增依赖，未访问网络，未改 scope 外文件。

未核对 e-Gov 的具体许可，因此配置仍禁止再分发；未运行全量测试。`scope` 是契约定义的非空 JSON 对象，其端点语义需由后续采集器按本文件配置解释。
