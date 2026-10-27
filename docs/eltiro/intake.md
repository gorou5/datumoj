# Eltiro 本机需求注入

注入只读本机 YAML/JSON，不访问需求里的网址，不执行文本、代码或指令。接口接受
`DemandSpec`（`eltiro.request/v0`），继续使用 `load_demand` 的 256 KiB 上限、UTF-8、
安全 YAML、别名拒绝和完整模型校验。已有需求契约不变。

## 数据目录与使用方配置

运行前由管理员在 `<data>/eltiro/consumers.yaml` 建立配置。该文件是本机数据，
不要提交到仓库。下面全是虚构标识，来源 id 必须替换为管理员已核验注册表中的 id：

```yaml
consumers:
  - id: example-analysis-site
    inbox: consumers/example-analysis-site/inbox
    outbox: consumers/example-analysis-site/outbox
    allowed_sources: [eu-eurostat, intl-imf-weo, intl-oecd, intl-wdi]
    accept_outflow: [raw_ok, derived_only, no_outflow]
    models:
      consumer: example-analysis-site
      authorized_sources: [eu-eurostat, intl-imf-weo, intl-oecd, intl-wdi]
      max_tier: medium
      allow_cloud: true
      allowed_providers: [none, ollama, cloud_api]
    max_calls_per_run: 200
    max_active_demands: 10
```

配置、使用方和嵌套模型配置均拒绝未知字段，不提供凭证值字段。
`models` 使用 003 的 `ConsumerModelConfig`：consumer 必须等于外层 id，
authorized_sources 必须排序、去重，且只能取 allowed_sources 的子集。
外层 `max_calls_per_run` 是使用方允许的需求预算上限，缺省 0；不修改 003 模型。
许可接受集合缺省 `[no_outflow]`。每个需求的接受集合必须是配置集合的子集；
档位、云端开关和调用次数不得超出配置，不会悄悄收紧后登记。

目录相对于数据目录，也可写数据目录内的真实绝对路径。按真实路径检查包含关系；
拒绝越界、路径穿越、目录符号链接，以及任意两个收发目录之间的相等或祖先关系
（包括同一使用方）。收发目录不能覆盖配置、数据库、审计或锁文件。
运行时逐级用目录文件描述符及 `O_NOFOLLOW` 重新打开，防止目录链接替换。

`<data>/eltiro/`、配置、注册表、审计、发件目录及归档目录由服务账号独占管理。
发布者只获得自己的收件目录中投放文件的权限，不应拥有数据根目录或内部状态的
写权限。目录缺失时以 0700 创建；回执、审计和锁文件以 0600 创建。已有目录和文件的
权限由管理员核对，服务不会擅自修改已有权限。这里的 consumer 身份来自配置与目录，
不是本机 CLI 调用者的账号认证；管理 CLI 的 list/show 可查看所有登记需求，仅供管理员。

## 调用

当前可直接运行模块入口；安装命令入口指向 `datumoj.eltiro.cli:app`。
本任务受执行环境禁止修改 `pyproject.toml` 的规则约束，没有添加该安装入口。

```sh
python -m datumoj.eltiro --data-dir data --sources-dir sources intake
python -m datumoj.eltiro --data-dir data --sources-dir sources submit --consumer example-analysis-site request.yaml
python -m datumoj.eltiro --data-dir data demands list
python -m datumoj.eltiro --data-dir data demands show three-gdp-reconciliation --version 1
python -m datumoj.eltiro --data-dir data demands deactivate three-gdp-reconciliation --version 1
```

全局 `--data-dir` 优先于 `DATUMOJ_DATA_DIR`，缺省 `data`；`--sources-dir` 缺省
`sources`，由管理员指定权威的、已核验的来源注册表目录，不能指定收件目录。
CLI 只读注册表 YAML，通过现有 `SourceSpec` 契约再次校验，不依赖 Rikolto 的实现或写入其状态。
服务 API 则接收只读 `Registry` 协议；测试使用其假实现。

intake 扫描全部使用方，输出 accepted/rejected 数量；submit 复制完整文件到指定收件目录，
只处理该文件，保留调用方的原文件。超大文件或符号链接在复制前即拒绝并写回执与审计。
submit 对拒绝返回退出码 1；intake 完成扫描返回 0，由计数报告文件拒绝；配置和 I/O 故障返回 1，
错误使用固定消息，不输出文件内容或 Pydantic/YAML 异常原文。
list 列出全部版本的登记元数据及 active 状态；show 未指定版本时显示最大版本
（包括停用版本），指定版本时显示完整已验证需求；deactivate 保留需求与历史。

## 权限与登记

偏好来源必须存在于权威注册表，且属于该使用方 allowed_sources。
需求不能以另一个 consumer 的身份登记；同一个需求 id 的所有版本归同一个使用方。
非 none 模型请求的偏好来源还必须属于 models.authorized_sources；请求云端必须有可用的
云端提供方权限，非 none 档位必须有提供方权限。

`accept_outflow` 是使用方接受哪些结果等级的集合，不是提升来源许可的授权。
例如同时接受 raw_ok 和 derived_only，不会把 derived_only 来源变成 raw_ok。
注册表许可始终权威；此入口不做模型调用和输出，不从需求复制许可或放开云端权限。
后续路由及输出仍须逐来源校验当前许可、cloud_model_ok、使用方授权与需求限制，取最严者。

`<data>/eltiro/demands.sqlite3` 使用标准库 sqlite3；主键为 `(id, version)`，
完整校验后保存规范化 JSON、首次登记 UTC 时间、使用方、首次文件 SHA256 和在用状态。
内容相同的重复登记幂等（不同空白、JSON/YAML 表达不改变需求内容）；内容不同拒绝。
每个 id 至多一个在用版本，替换版本不额外占用名额。检查上限与写入在同一个
`BEGIN IMMEDIATE` 事务中；失败不留下半条记录。返回的模型是重新解析的副本。
存储协议直接调用属于受信任内部接口；收件入口才应用使用方权限与上限。
直接 register 未提供文件哈希时记录规范化需求内容的 SHA256；所有文件注入均传入实际文件哈希。

## 文件、回执与审计

扫描跳过隐藏项与目录；普通文件名限 1–200 个 ASCII 字母、数字、点、连字符、下划线，
首字符必须是字母或数字。符号链接和其他特殊文件拒绝，绝不打开链接目标或阻塞读取 FIFO。
先 lstat，再用 `O_NOFOLLOW | O_NONBLOCK` 打开并核对 fstat 身份；读取前先决定是否超限。
超大普通文件仅流式计算 SHA256，不保留全文、不解析；读取最多初始大小加一字节，
不会跟随无限增长文件。普通文件哈希是实际读取的原始字节，符号链接哈希是链接文本的
文件系统编码字节，特殊文件和无法安全读取的文件哈希为 null，不能伪造为目标文件哈希。
文件变动时拒绝；若目录项已被替换，保留新目录项待下次扫描。
发布者应先写临时隐藏文件，再原子改名为正式文件，发布后不再修改。

通过的文件原子 rename 到 `inbox/processed/<随机票号>/<原文件名>`，拒绝的文件到
`inbox/rejected/<随机票号>/<原文件名>`；不安全文件名在归档中用 `demand.bin`。
每次创建私有的新票号目录，不覆盖同名文件；归档必须和收件目录在同一文件系统。
提交复制和 JSON 回执均先写临时文件，再原子发布完整文件。

每个发件目录下的 `<随机票号>.json` 包含：

```json
{"conclusion":"accepted","demand_id":"example-demand","version":1,"file_sha256":"abababababababababababababababababababababababababababababababab","reason_code":"accepted","message":"Demand registered."}
```

模型校验失败但已解析出合法身份时，也可回显 id/version；consumer 不符时身份置 null。
固定原因代码包括 invalid_demand、consumer_mismatch、source_forbidden、outflow_forbidden、
model_forbidden、active_limit、version_conflict、file_too_large、symlink、unsafe_filename、
not_regular、file_unreadable、file_changed。回执无需求原文、文件路径、异常输入或其他使用方信息。

`<data>/eltiro/audit.jsonl` 以 `O_APPEND` 追加，每条仅含 UTC time、consumer、file_sha256、
conclusion、reason_code；不记录需求文本、模型凭证、文件路径或其他使用方的身份。
本机进程锁串行化收件操作；审计和回执刷盘成功后才移走原输入。如果回执或归档 I/O 失败，
输入仍可重试，登记幂等；中断重试可能追加重复审计/回执，不能把文件系统与 SQLite 的多个
写入冒充成单一跨存储事务。执行错误不会自动删除或回滚已持久化的证据。

配置、历史需求、归档、回执和审计为本机业务元数据，为版本追溯与审查保留，未设置自动清理；
管理员按本机留存政策备份后清理，不自动移除审计或停用需求。机器可读模块说明见
`schemas/eltiro/intake.module.json`，配置 Schema 是 ConsumerConfig / ConsumersConfig。

## 验证

实现继承 001 的 DemandRegistrySuite。全部测试离线、临时文件使用 tmp_path：

```sh
make check T=tests/regressions/test_audit_wisteria_801.py
make check T=tests/eltiro
make check T=tests
```

实现依赖 POSIX 的目录文件描述符、O_NOFOLLOW、flock 及同文件系统原子 rename，
支持当前 macOS/Linux 本机部署；不声称支持 Windows。不提供网络服务、推送、匹配、执行或输出包。
