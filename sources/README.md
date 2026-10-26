# 来源注册表

每个来源放在一个 `sources/<id>.yaml` 文件中。文件内容直接按 `datumoj.core.contracts.SourceSpec` 校验；`id` 必须符合契约格式，且与文件名一致。注册表只保存配置，不读取或展开环境变量。

## 字段

| 字段 | 说明 |
| --- | --- |
| `id` | 稳定来源 ID，格式为小写国家/区域前缀及连字符段，例如 `jp-egov-law`。 |
| `name`、`country`、`kind` | 显示名称、ISO 风格的小写国家/区域代码、采集器类型。 |
| `base_url` | 来源 API 或站点入口。 |
| `allowed_hosts` | HTTP 请求允许访问的主机名白名单。 |
| `rate_per_sec` | 每秒请求数，必须大于 0；默认值为 1。 |
| `max_total_bytes`、`max_files` | 单来源累计字节数和文件数上限，必须大于 0。 |
| `schedule` | 可用值：`hourly`（每小时）、`daily`（每天）、`weekly`（每 7 天）、`monthly`（每 30 天）、`manual`（不自动运行）；可留空（永不到期）。 |
| `scope` | 非空对象，列出该采集器允许请求的数据范围或端点。 |
| `needs_key` | 无需凭据时留空；需要时只写 `env:UPPERCASE_NAME`，不写 Key 值。 |
| `retention_note` | 来源对保留期限的说明；未知时留空。 |
| `license` | `LicenseInfo` 对象，含许可 `name` / `url`、`attribution`、`usage_scope`、`redistribution`、`verified_at` 和 `notes`。 |

`license.usage_scope` 默认 `internal_only`，`license.redistribution` 默认 `unverified`。只有逐项核实许可后才能填 `allowed`；此时必须同时填写许可名称、官方许可 URL、署名文本和 `verified_at` 日期。未确认可再分发时，使用范围只能是 `internal_only`。`verified_at` / `notes` 分别对应任务字段 `license_verified_at` / `license_notes`，两者放在嵌套的 `license` 对象中以遵循契约。

## 许可核对方法

针对具体来源和具体数据，查阅提供方的官方利用规约、API 条款及必要的适用法律；记录能直接支持结论的许可名称、官方 URL、要求的署名文本、核对日期和限制。确认许可允许再分发后，才将 `redistribution` 改为 `allowed`；如有疑问或规则冲突，保留 `unverified` / `internal_only`。不得把“公共データ利用規約”套用到所有日本来源；例如国土地理院数据还需核查測量法等适用条件。

## 示例

```yaml
id: jp-egov-law
name: e-Gov 法令 API v2
country: jp
kind: egov_law
base_url: https://laws.e-gov.go.jp/api/2
allowed_hosts: [laws.e-gov.go.jp]
rate_per_sec: 1
max_total_bytes: 5368709120
max_files: 10000
schedule: daily
license:
  name: null
  url: null
  attribution: null
  usage_scope: internal_only
  redistribution: unverified
  verified_at: null
  notes: 尚未逐项核对
scope:
  endpoints: [/laws, /law_data/{law_id}, /law_revisions/{law_id}]
needs_key: null
retention_note: null
```
