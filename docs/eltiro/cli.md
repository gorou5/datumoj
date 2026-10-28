# Eltiro 本地命令行

全部命令离线运行。页面须由宿主先下载为本地 UTF-8 HTML；CLI 不下载页面、不调用模型，且没有 `--model` 参数。概念说明只提供列表，生成需待后续的模型上下文约定。

全局选项放在命令前：

```text
eltiro [--data-dir data] [--sources-dir sources] <command>
```

`--data-dir` 也可由 `DATUMOJ_DATA_DIR` 设置。使用方配置在 `<data-dir>/eltiro/consumers.yaml`，来源目录为宿主维护的权威 YAML 注册表。CLI 保留现有状态路径校验；新增 SQLite 库和日志旁文件同样拒绝符号链接、非普通文件和硬链接。

## 命令与 Python 接口

以下列出全部命令。标为既有的命令保留原参数、输出和错误行为。

| 命令 | 调用的 Python 接口 | 成功输出 |
| --- | --- | --- |
| `intake`（既有） | `IntakeService.scan` | 接受／拒绝计数 |
| `submit --consumer C <file>`（既有） | `IntakeService.submit` | 提交回执；拒绝时退出 1 |
| `demands list`（既有） | `SQLiteDemandRegistry.list_records` | 登记记录列表 |
| `demands show <demand_id> [--version N]`（既有） | `SQLiteDemandRegistry.get`；无版本时用 `list_records` 选最大版本 | 需求 JSON |
| `demands deactivate <demand_id> --version N`（既有） | `SQLiteDemandRegistry.set_active` | 停用回执 |
| `matches queue [--consumer C] [--demand D]`（既有） | `SQLiteDemandRegistry.list_records`、`SQLiteMatchStore.for_demand` | 待人工确认且未自动绑定的候选 |
| `matches confirm <demand_id> --version N --indicator K --series JSON --generation N`（既有） | `SQLiteMatchStore.set_confirmation` | `confirmation` 状态 |
| `matches reject <demand_id> --version N --indicator K --series JSON --generation N`（既有） | 同上 | `confirmation` 状态 |
| `run <demand_id> [--version N] [--generation-token T]`（既有） | `run_registered` | `run_id`、`row_count`、`gap_count` |
| `runs list`（既有） | `RunStore.list` | 运行记录列表 |
| `runs show <run_id>`（既有） | `RunStore.show` | 运行记录 |
| `package <run_id>`（既有） | `package_run` | 打包回执（含路径） |
| `refresh --previous M --as-of YYYY-MM-DD [--generation-token T]` | `open_generation_views`、`rematch.refresh` | `added`、`modified`、`deleted`、`rematched`、`carried`、`alerts` 计数 |
| `alerts list [--consumer C] [--demand D] [--all]` | `alerts.list_alerts` | 提醒列表（含证据身份、规则名及确认状态） |
| `alerts ack <alert_id>` | `alerts.ack_alert` | `{"acked": true}` |
| `gaps show <demand_id> [--version N] --generation N` | `gap_report`；通过 `SQLiteDemandRegistry` 取指定／最大版本 | `path`、`indicator_count`、`gap_count` |
| `gaps report --generation N` | `aggregate_gaps` | `path`（JSON）、`markdown_path`（Markdown） |
| `notes list --generation N` | `list_concept_notes` | 已保存说明、模型生成标签及模型／缓存出处 |
| `pages record <verify\|cases> <file>` | `parse_cases`、`record_cases` | `added`、`changed`、`disappeared`、`unchanged`、`failures` 计数 |
| `drafts make <verify\|cases> <file> <case_no> --consumer C` | `parse_cases`、`draft_for_case`（`router=None`） | `path`、`registered: false`、后续提交提示 |
| `drafts list` | `list_drafts` | `paths`、`registered: false`、后续提交提示 |

JSON 输出均写到标准输出。新增命令不输出需求正文、来源正文或观测数值；`notes list` 按用途显示已存的模型说明，并保留“模型生成”标签。

## 代次与隔离

`refresh` 只调用一次 `open_generation_views(data_dir, token)`。不传 token 时固定当前已发布代次；传 token 时严格选择该保留代次，缺失或损坏直接失败，不回退到当前代次。新代次编号从返回的 `GenerationToken` 解析；`M` 仅用于 Eltiro 画像／匹配库的上一代读取，不用于寻找 Rikolto 历史快照。宿主须事先准备所选代次的 Eltiro 画像；命令不重建画像。传给 `refresh` 的三个 Rikolto 视图均来自同一个固定代次，`vector=False`、`model=False`。

`gaps` 只读需求、匹配、画像及本地来源注册表，`notes list` 只读取 Eltiro 概念说明库；它们的整数 `--generation` 不打开 Rikolto，也不查找 token。库的打开／初始化行为沿用现有 Python 接口，不生成匹配或概念说明。

`alerts list` 默认排除已确认提醒，`--all` 包含已确认项。`--consumer` 与 `--demand` 同时给出时取交集。`gaps show` 的报告只包含指定需求及该使用方授权候选，`gaps report` 则是宿主查看的全局规划汇总。

单需求缺口报告保存到 `<data-dir>/eltiro/gaps/<demand_id>-vN-gN.json`，汇总保存到 `<data-dir>/eltiro/reports/gaps-gN.{json,md}`。`gap_count` 是报告内缺口条目数量，一个指标可能有多条。

## 页面和草稿

页面只接受有界普通本地文件，拒绝符号链接、设备、FIFO 和超限文件；解析、案例字段验证和变化判断全部复用 810 接口。`pages record` 对能解析的案例登记哈希，解析失败计入 `failures`，保留接口原有的失败身份保护规则；整页不可解析则命令失败。

`drafts make` 从此次传入文件解析完整案例，不依赖 `pages record` 的哈希记录。验证页编号如 `V07`，案例页编号如 `'#007'`（在 shell 中须引号保护 `#`）。编号缺失、重复或目标案例解析失败均报错；其他案例失败不阻止有效目标生成草稿。使用方必须出现在本地配置中。

草稿保存在 `<data-dir>/eltiro/drafts/`，不登记或激活需求，也不向使用方 outbox 写入。检查并补齐草稿后，用 `eltiro submit --consumer C <edited-file>` 提交改好的文件。草稿中的来源文字保持为数据；CLI 不执行页面指令，也不调用模型。重复／冲突处理沿用 `draft_for_case` 的保留人工修改规则。

## 错误输出

新增命令的参数解析、配置、输入验证、未知身份和存储错误统一向标准错误输出以下固定行，退出码为 1：

```text
Eltiro operation failed; check configuration and local state.
```

不附加异常消息、traceback、输入参数、需求原文、观测数值、来源文字或路径细节。帮助输出正常可用。既有命令保持原规则，包括 `submit` 的拒绝回执及 `package` 的固定错误码／来源 ID 回执。
