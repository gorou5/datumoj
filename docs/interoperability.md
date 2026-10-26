# 互操作性规范：三个统一标准

> 设计原则：**统一接口和协作规则，不统一实现方式。**
> 不要一开始设计庞大框架 — 先用 `examples/` 中的最小真实场景验证，再演进。

## 1. 统一描述标准：让模块能被发现

每个数据源、连接器、Skill、Agent、MCP 工具等模块，都应有**机器可读**的描述。
注册层 Schema 见 `schemas/registry/`：

- 数据源 → `schemas/registry/data-source.schema.json`
- 可复用模块 → `schemas/registry/module.schema.json`

注：`schemas/` 根目录是内部数据契约，由 `src/datumoj/rikolto` 的模型导出并有契约测试锁定
（`export_schemas` 会清理多余的 `*.schema.json`），注册层标准不要直接放根目录。

### 1.1 公共字段（所有模块）

`id`（全局唯一，`[a-z0-9-]`）、`name`、`kind`、`version`（语义化版本）、
`description`（解决什么问题、一句话）、`status`（`active`/`experimental`/`deprecated`）、
`license`、`maintainers`。

### 1.2 类型专属字段

- 数据源：`country_region`、`publisher`（发布机构）、`official_url`、
  `access_method`（`api`/`download`/`web`/`bulk`）、`data_format`、
  `rate_limit`、`auth_required`、`last_verified`（最后验证时间）。
- 连接器/工具：`inputs`、`outputs`、`invocation`（调用方式）、
  `runtime`（运行环境）、`permissions`、`dependencies`。

未知字段填 `"unknown"`，**不得编造**。字段宁缺勿假。

### 1.3 描述必须能回答的七个问题

1. 这个模块解决什么问题？ 2. 我能否直接使用它？
3. 我需要提供什么输入？ 4. 它会返回什么结果？
5. 它依赖什么权限和运行环境？ 6. 它是否适合我的场景？
7. 我如何验证它能正常工作？（`test` 字段给出最小验证步骤）

### 1.4 已有标准优先

能用成熟标准就不用自造：数据集描述参考 [DCAT](https://www.w3.org/TR/vocab-dcat-3/)、
通用元数据参考 [schema.org](https://schema.org/)。本项目的 JSON Schema
只覆盖协作必需的最小字段，与外部标准字段名冲突时优先采用外部标准命名，
并在文档中注明映射关系。

## 2. 统一调用标准：让模块能组合

### 2.1 调用边界（每种调用形式都要声明）

- 输入/输出：参数名、类型、必填约束、示例值；
- 错误格式：机器可读的错误码 + 人类可读信息 + 可重试性标记；
- 超时行为：默认超时、超时后的状态（未知/已回滚/需查询）；
- 认证与权限边界：需要什么凭证、最小权限是什么；
- 版本兼容性：遵循语义化版本；破坏性变更必须在 PR 中声明并检查调用方；
- 追溯信息：返回结果必须携带来源引用（见 §3）。

### 2.2 协议无关

允许：函数/CLI、HTTP API、MCP、Agent 工具调用、文件 IO、消息队列等。
**不强制统一协议**，但不同协议之间应可通过适配器连接。
同一连接器应能被 Cloudflare Worker、本地 Agent、MCP 宿主等多种方式调用；
只能在特定环境运行的模块必须**明确声明限制**。

### 2.3 管道约定（轻量）

- 外部调用必须设置超时与重试上限（含退避）；失败必须可被识别和分类。
- 重复执行不应产生意外副作用；不支持幂等的外部接口必须声明风险。
- 日志包含：时间、模块 id 与版本、输入摘要（脱敏）、来源引用、结果状态。
  **日志中不得出现完整凭证。**

## 3. 统一证据标准：让数据可追溯

### 3.1 每条事实的证据链

来自政府公开数据的事实，应尽可能记录：

`来源机构`、`官方来源URL`、`数据集/接口标识`、`原始记录ID`、
`采集时间`、`数据发布时间`、`处理与转换步骤`、`模块id与版本`、
`许可与访问限制`、`验证状态`。

**关键区分**（禁止混淆）：

- **采集时间 ≠ 数据发布时间**；
- **搜索结果 ≠ 已核实的事实**；
- 同一份文件出现两家机构 **≠** 它们有股权/控制/合作关系；
- 采购中标记录只证明该文件载明的采购关系，不外推其他关系。

### 3.2 断言级别

每条关系/事实标注其一：

- `proven` — 来源明确证明；
- `inferred` — 多证据推导（注明依据）；
- `unverified` — 待验证。

未知就是未知，明确标记，不得把推测写成事实。

### 3.3 版本与变更

来源发生变化时，保留必要的历史版本信息以便理解结果为何变化；
但不得为追溯而无限期保存全量原始数据 — 保存方式服从许可、隐私与必要性原则。

## 4. 标准演进

- 本规范是 v0.1（最小可行）。修改规范本身也走 PR + 讨论。
- 新增字段时保持向后兼容；废弃字段先标记 `deprecated` 一个版本周期。
- 每季度（或每 20 个模块）回顾一次：哪些字段从没人填、哪些问题反复出现，
  据此裁剪或补充。

## 与 DATUMOJ 内部模型的映射

`rikolto registry export` 将 `sources/*.yaml` 转成 `catalog/<id>.json`，输出仅包含
`schemas/registry/data-source.schema.json` 定义的字段。字段映射如下：

| `sources/*.yaml` | 注册层描述 |
| --- | --- |
| `id`、`name` | `id`、`name` |
| `country` | 大写后的 `country_region` |
| `license.attribution` | 去掉 `出典：` 前缀、括号中的补充说明和引号中的产品名后作为 `publisher`；缺失时为 `unknown`。无法从文本识别单一机关时保留来源提供的机构描述，不推断具体机构。 |
| `base_url` | `official_url` |
| `kind` | `ckan`、`egov_law`、`stat_dashboard`、`jma_feed` → `api`；`estat_files` → `download`；`gov_web` → `web`；其他值 → schema 中最接近的 `other`。 |
| `rate_per_sec`、`needs_key` | `<数值> req/s` 格式的 `rate_limit`、布尔值 `auth_required` |
| `license.name`、`license.url` | `license.name`、`license.url`；名称缺失时为 `unknown`。URL 缺失时省略该可选字段，以满足 URI 格式约束。 |
| `license.redistribution` | `allowed` → `allows_redistribution: true`；`forbidden` → `false`；`unverified` → `"unknown"` |
| `license.attribution` | 存在时 `requires_attribution: true`，缺失时为 `"unknown"` |
| `license.verified_at` | `last_verified`；缺失时为 `unknown` |
| 固定值 | `status: experimental` |

注册层描述仅表示来源注册配置，不取代内部来源、许可或采集记录。

`Citation` 与 §3.1 证据链对应如下：

| `Citation` / 内部字段 | §3.1 证据链字段 |
| --- | --- |
| 来源机构 | 来源注册表（`sources/*.yaml`） |
| 官方 URL | `resource_url` |
| 原始记录 ID | `snapshot_sha256` + `locator` |
| 采集时间 | `fetched_at` |
| 发布时间 | `published_at` / `period` / `time_basis` |
| 许可 | `license` / `usage_scope` |
