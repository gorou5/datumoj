# ADR 0006：Eltiro 的声明式契约、信任边界与许可闸门

- 日期：2026-10-27
- 状态：已定（2026-10-28 人审通过，检查点 WISTERIA-901 / m0）
- 依据：[0.4 计划](../plans/0.4-wisteria.md) 第 3.3、6.1–6.6 节，以及两个只读需求样例。

## 边界与存储

Rikolto 负责抓取、快照、解析、索引、观测值和注册表。Eltiro 接受外部需求，维护序列画像与匹配记录，后续负责固定代次上的取数、运算和输出。001 只提供数据模型、协议、内存假实现和合格测试；不提供匹配、SQL、执行器、输出器、目录监听或许可闸门实现。

Eltiro 只读 Rikolto **已发布**的代次。一次运行固定 `generation` 与 `generation_token`，每次只读查询校验 token；失效、不可用或解析到多个输入身份时停止，不切换到最新代次。代次不等于来源发布：一个代次可以包含同一序列的多次发布。002 定义发布身份及 token 格式；这里都仅保存有长度限制的不透明字符串。

Eltiro 持久化资料应放在其独立数据根目录 `eltiro/` 下（需求、画像、匹配、运行及输出），不得放进 Rikolto 的数据库，也不得写它的库、快照或发布指针。001 不创建这些持久化目录，也不指定 SQLite 等存储格式；协议假实现仅用内存。部署根路径、注入目录与消费者授权由 801 及部署任务决定。

Rikolto 只新增 `Outflow` 枚举及 Schema 导出。`LicenseInfo`、来源 YAML、Rikolto 接口协议与其他模型保持原定义；许可字段及旧记录迁移属于 102。

## 需求与信任边界

沿用样例的字段名：`schema: eltiro.request/v0`，需求身份为 `(id, version)`，同版本不可覆盖成不同内容。Python 属性 `schema_version`、`DemandPeriod.start/end` 分别以 `schema`、`from/to` 为外部别名。JSON Schema 按外部别名导出。缺省模型调用预算为零、云端禁用、许可为 `no_outflow`；缺省不代表获得任何授权。

所有模型继承 `extra="forbid"`、`frozen=True`；容器仍按 Pydantic 惯例可变，存储边界重新校验并返回深度分离的副本。未知字段、可执行标签、未知运算、未知参数、无效参数均拒绝。没有 SQL、表达式、脚本、导入、模板执行或回调字段。输出格式只有 Parquet / CSV，文件名只允许包内 basename，不允许目录、路径穿越或原文附件。

文本中的命令、链接和代码样式内容仍是**数据**，不调用 shell、不 eval、不访问链接，也不能改变消费者、许可、路由或可用来源。不能通过“黑名单”删改正例里的合法表号：希腊样例正例中的 `gov_10dd_edpt1` 原样保留。指标没有 `table` / `table_name` / `table_key` 等绑定字段；来源偏好只接受注册表 id 的语法，是否已登记及是否向该消费者开放由 801 验证。本契约不把一段概念文字提升为表绑定。

`load_demand(path_or_bytes)`：对 bytes 先检查长度；对文件先 stat，再有界读取至上限加一个字节，再次检查，之后才 UTF-8 解码和 `yaml.safe_load`（同样支持 JSON），最后完整 Pydantic 校验。不按扩展名选解析器，不执行 YAML 对象标签；大小/编码/YAML 语法错误为 `ValueError`，模型错误为其子类 `ValidationError`，文件 I/O 错误保留。路径由调用方给出，需求没有 include 或路径字段。安全加载不等于授权或文本清洗。

安全构造前先扫描并拒绝 YAML 别名：需求没有共享/循环对象图的语义，不能利用别名合并放大绕过文件大小的资源边界。扫描同样在字节大小检查之后进行。

上限常量在 `datumoj.eltiro.models`，按结构字段的 Schema 上限及模型跨字段校验共同约束：

| 常量 | 值 | 用途 |
|---|---:|---|
| `MAX_FILE_BYTES` | 262144（256 KiB） | 注入文件原始字节，解析前检查 |
| `MAX_INDICATORS` | 64 | 每份需求的指标数 |
| `MAX_ENTITIES` | 256 | 国家和地区合计；画像地区覆盖列表 |
| `MAX_OPERATIONS` | 128 | 每份需求或出处链中的运算数 |
| `MAX_TEXT_LENGTH` | 4096 字符 | 用途、概念、说明、正反例、理由、倍率依据 |
| `MAX_LABEL_LENGTH` | 256 字符 | 单位、名称、表键、观测身份、发布标识、token 等短文本 |
| `MAX_ID_LENGTH` | 128 字符 | 外部 id、来源 id、指标/运算符号 |
| `MAX_ITEMS` | 256 | 来源偏好、输出字段、维度、概念编号、依据、文件、引用及其输入等列表/映射 |
| `MAX_LANGUAGES` | 32 | 多语言名称、说明；语言键最长 35 字符 |
| `MAX_LAG` | 120 | 滞后周期；相关运算允许正负 |
| `MAX_WINDOW` | 1200 | 移动平均、相关窗口，至少 2 个周期 |
| `MAX_CALLS_PER_RUN` | 1000 | 每次运行最大模型调用次数，缺省 0 |

另外：输出格式最多 2 种、许可最多 3 种、提醒最多 4 种、分开维度最多 5 种、模型生成字段最多 11 种。国家码同时检查大写三位形状与 ISO 3166-1 alpha-3 的 249 个已分配代码（包括地区）；不接受任意三字母字符串。地区另带代码体系。需求 id、consumer、来源 id 只允许小写字母、数字、连字符，来源另遵守 Rikolto 的 SourceId 语法。样例的指标 key、运算 id 和引用是列符号，另允许下划线，不能是表达式。

Schema 表达结构与局部参数限制；实体合计、有效日历日期与范围、引用、环、口径保护、许可汇总等跨字段规则必须再走 Pydantic 校验，不能把单独通过 JSON Schema 当成登记成功。未来入口不得绕过 `load_demand` 的字节限制。

## 固定运算与引用

| `op` | 参数与含义 |
|---|---|
| `rescale` | `input`, `target_scale > 0` 且有限；数量级显式统一 |
| `diff` | `left`, `right`, `sign=minus/plus`，缺省相减；样例的存量流量调整用 plus |
| `diff_lag` | `input`, `lag` 为 1–120 周期；相对滞后值的差 |
| `ratio` | `left / right`，仅接受这两个引用 |
| `growth_rate` | `input`, `lag=1`，范围 1–120；相对滞后值的增长率 |
| `source_spread` | `input`；同对齐组各来源相对中位数及最大最小差 |
| `revision_diff` | `input`；相对首次发布的修订差 |
| `mirror_diff` | `left`, `right`；两个显式镜像序列的差 |
| `residual` | `left`, `right`；实际值减显式基准序列 |
| `moving_average` | `input`, `window` 为 2–1200 周期 |
| `lead_lag_corr` | `left`, `right`, `lag` 为 -120–120，`window` 为 2–1200；正 lag 表示将 right 滞后后与 left 对齐 |

引用指向指标 key 或运算 id，命名空间共用且唯一。允许前向引用和共享依赖；检查悬空引用并按可解析依赖逐层剥离，剩余节点代表成环。无内联公式、任意参数字典或插件名。倍率只可取来源元数据，未确认以 `Scale.multiplier=None` 表达，不能默认为 1；已确认倍率必须有依据。执行器应记录实际换算依据并将未确认倍率作为缺口，禁止悄悄估算。

001 不计算数值。804 必须遵守缺失值为空、注记原样保留，不能补零；除零、窗口不足、频率不齐、单位或调查不兼容应报缺口或失败。增长率输出单位、统计细节等执行规范由 804 补足并测试，不接受需求自定义代码来填补。

## 画像、匹配和出处

序列完整身份为 `source_id + table_key + indicator + indicator_code + fixed_dimensions`；维度字典的顺序不影响身份。画像包含来源概念编号、多语言名称/说明、调查与口径、单位和倍率、地区与时间覆盖、频率、可得程度、生成代次及模型生成字段名单。模型不能声称生成序列的来源身份。画像不保存许可；通过 `series.source_id` 到权威注册表查询。

匹配记录以 `(demand_id, demand_version, indicator_key, series, generation)` 定位；三档为 deterministic / vector / model，保存有限的 0–1 置信度、理由、画像字段及快照/locator 依据。记录的序列身份和代次定位画像条目，依据的来源必须与候选来源一致。

`confirmation` 是人工状态 pending / confirmed / rejected。`auto_bind` 与人工状态分开，缺省 false；口径、单位、调查的 compatibility 缺省 unknown，三个全为 same 且未 rejected 才能 auto_bind。不同或未知只可保留候选；高置信度或模型档位不能绕过校验。人的 confirmed 决定不能被匹配器覆盖；rejected 同时清除 auto_bind。记录声明的兼容性仍需 803 根据证据判断，本模型不实现判断算法。

输出清单包含需求版本、代次、token、有时区的运行时间、模型/版本/档位、许可汇总、数据文件名与 sha256。引用编号解析到每个输入的 `(source_id, table_key, obs_id)`、`snapshot_sha256`、不透明 `release_id` 与带参数的运算链；不能只用 obs_id 关联或去重，也不附原文。运算链中的符号沿用需求命名空间，804 应从对应需求的已验证 DAG 构建链，不得从清单加载并执行未经需求校验的运算。

没有观测值的运行可以记录空的数据文件列表和引用索引，许可仍缺省 no_outflow；缺口报告的实际生成与交付由后续输出任务处理。时间范围的 to 为年或月时包含其整个年/月，不把缺少日字段误作只到该月第一天。

## 许可闸门

`Outflow` 放在 Rikolto 的 core/models.py，Eltiro 需求和输出清单共用：

- `raw_ok`：仍须遵守来源条款、署名与消费者授权。
- `derived_only`：允许整理后的数值（对齐、规范化、运算后的序列），不允许 raw 文件、原文、文本片段；出处只含引用索引。
- `no_outflow`：不得进入任何交付包，只可用于匹配和内部校验。

这是计划 3.3 的界线：整理过的数值是最终结果，即使与来源的原值相同；raw 文件、原文、片段是原始物。不能以“做过运算”为理由放出原始物，也不能以“数值未变化”为由把合规整理后的数值误判成原始物。

缺省、未知或未核准均按 `no_outflow`；未开放云端均按 false。需求的 `accept_outflow` 只是使用方接受的范围，**不是许可授权**。许可汇总是审计快照，要求总等级不得比任何已列来源宽松，缺少输入来源许可时总等级必须 no_outflow；画像、输出、缓存中的记录不能覆盖注册表。001 不修改 LicenseInfo，因此新许可字段的正式读法仍等待 102。

805 在写出前根据权威 `LicenseInfo`、消费者授权、需求接受范围逐列检查完整血缘；混合来源取最严，缺许可或失败时整包不交出，不能先交部分文件。将数据送入云端模型也算流出：no_outflow 不进云端，即使 cloud_model_ok 为真；cloud_model_ok 为否也不进。003/806 负责路由与云端前置检查，消费者只能收紧这些规则。001 的结构校验不是一次许可放行。

## 模型路由

003 定义 `RoutingPolicy`、`ConsumerModelConfig`、`RouteDecision`、`RoutePlan`、`ModelCacheKey`、
`ModelResult` 及 `ModelRouter` / `ModelProviderClient` / `ModelCache` 协议。102 已提供权威
`LicenseInfo.outflow` / `cloud_model_ok`，002 的 `GenerationToken` 在本契约中直接复用。
只提供内存假实现和离线合格测试，不解析凭证、不启动 Claude Code、不连接 Ollama 或云端 API。

提供方只有 `none`、`claude_plan`、`ollama`、`cloud_api`；Claude 套餐与云端 API 都是云端。
策略按步骤（必须包含 `match_judge`、`concept_note`，可增加步骤名）及 low / medium / high
难度配置目标，每个目标固定提供方、模型名和版本。凭证位置只允许环境变量名
`credential_env`，没有凭证值、端点、脚本或命令字段；拒绝未知字段、密钥形状、Bearer、
凭证赋值及不合环境变量语法的值。标识符检查采用保守拒绝，不能据此把任意文本当作安全凭证容器。
`RoutingPolicy.content_hash` 是配置规范 JSON（键排序、紧凑分隔、UTF-8）的 SHA256，
由内容推导，不接受配置方传入的哈希覆盖。选定的决策另保存此哈希。

宿主把**当前注册表**的 `LicenseInfo` 传给路由器；消费者不得提交许可替代品。
`ConsumerModelConfig` 中的消费者身份及已授权来源范围也必须由宿主授权层提供，不能从来源正文读取。
消费者的提供方白名单、max_tier、allow_cloud 只能收紧需求预算与注册表权限：

- `no_outflow` 不得进入任何云端提供方，即使 `cloud_model_ok=true`。
- `cloud_model_ok=false` 只能选 `ollama` 或 `none`。
- 需求或消费者任一 `allow_cloud=false` 都禁云端；档位不得高于两者任一 `max_tier`。
- 缺少注册表许可或来源不在已授权范围内，进入人工队列，不调用模型。
- 混合来源输入先按来源许可拆开。`ModelInput.parts` 明确各来源的独立内容；
  只有调用方能证明可拆时才设 `splittable=true`。假实现逐来源分区，各部分单独路由，
  不把受限部分交给云端。无法独立拆分时整个输入进入人工队列，不以最宽松来源放行。

`plan` 在运行前一次选定所有路由，尚不调用模型。从请求难度开始，按
high → medium → low → none 逐级检查（low 从 low 开始），超过档位上限、提供方不被允许
或许可不满足就继续降档；配置的 none 也继续向低档检查。到最后仍无合格目标则记录
none 决策及降档原因，并生成 `ManualQueueItem`，绝不调用 none。
未配置的扩展步骤也进人工队列。`RouteDecision` 保存消费者、完整授权范围、代次 token、
输入、选定目标、档位、策略哈希及降档原因；`RoutePlan` 以 `run_id` 固定这些决策和预算。
同 run_id 重复 plan 只能返回原计划，任何上下文变更都抛 `RouteInvalid`。

`invoke` 只能使用已保存计划；包括重试在内都不重新选目标。提供方缺失或不可用抛
`ProviderUnavailable`，本次运行失败，不切换到本地或另一云端。每次实际尝试（含失败重试）
消耗一次 `max_calls_per_run`，缓存命中不消耗；额度用完后剩余调用进入人工队列，
零预算在计划阶段就进入人工队列。806 的持久实现必须保存计划和调用计数，重启不能重置预算。

缓存身份恰好八项：

| 项 | 内容 |
|---|---|
| 使用方 | consumer |
| 授权来源范围 | 完整、排序且去重的 authorized_sources，不能只记本次实际来源 |
| 固定代次 | GenerationToken，包含 generation 与 manifest_sha256 |
| 输入哈希 | 规范 JSON 的 SHA256，包含步骤、难度及各来源内容 |
| 提供方 | claude_plan / ollama / cloud_api |
| 模型及版本 | ModelIdentity(model, version) 作为一项 |
| 路由策略哈希 | RoutingPolicy.content_hash |
| 提示词版本 | prompt_version |

`ModelCacheKey.digest` 同样取八项规范 JSON 的 SHA256。不同使用方、授权范围、代次、
策略、模型版本或提示词版本不能命中旧结果。缓存存储本身不是授权层；每次读取前，
路由器先检查当前消费者身份和完整授权范围与决策一致，并从当前注册表重验来源许可。
许可撤回、来源缺失、授权范围变化、当前消费者进一步禁云或降档时，旧路由抛
`RouteInvalid`，不能为了读缓存临时换路由。许可记录或缓存中的审计副本都不能覆盖注册表。

同代次重跑必须引用原 run_id，`rerun=True` 只读原路由的缓存；缺失抛 `RerunCacheMiss`，
即使有预算也不重新调用。新的授权、策略或代次应显式建立新运行，不作为原运行的重跑。
`ModelResult.fields` 的每个字段使用 `ModelField.model_use` 保存提供方、模型、版本、档位；
路由器校验返回值及缓存值均与固定决策一致。已有 `ModelUse` 提供方 id 语法扩展为允许下划线，
保留原有连字符 id 的兼容性；路由配置本身仍只接受上述四种提供方。

`ModelRouterSuite` 通过 `make_router` 工厂注入实现，直接包含全部许可硬规则、混合来源、
降档到 none、失败不换路由、失败重试计费及预算耗尽用例，并检查缓存隔离、读前复核和重跑。
`tests/eltiro/` 用假实现继承整个套件，806 必须同样继承；回归测试复用套件而不能替代它。
`ModelProviderSuite`、`ModelCacheSuite` 分别检查提供方输出标注与八项缓存身份和副本隔离。
Schema 使用既有导出入口重新生成；跨字段规则仍必须完整通过 Pydantic 验证。

## 兼容性及后续

Eltiro 是新增的 v0 契约。样例原样读入，补充的缺省字段只收紧行为；没有迁移现有 Rikolto 数据。删除 119 已完成改名验收的 schemas 整目录哈希锁，保留重新导出逐字节一致断言，让合法契约新增可继续验收。

002 的发布身份和 token、102 的许可字段及 003 的路由契约已由各自任务定义。
后续保留：801 的授权与注入目录，802 的画像提取，803 的语义判断与增量匹配，
804 的运算数值语义及输入血缘组装，805 的实际许可闸门、缺口/差异报告和输出写出，
806 的真实模型适配、持久路由与缓存、提示词及运行预算管理。本契约任务没有实现这些生产功能。
