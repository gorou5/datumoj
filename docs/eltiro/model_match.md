# Eltiro 可选模型辅助（807）

```yaml
id: eltiro-model-assistance
name: Eltiro candidate judgments and separate concept notes
version: 1
status: maintained
runtime: Python; standard-library sqlite3; existing project dependencies
inputs: [DemandSpec, ConsumerConfig, generation-frozen profiles and read views, ModelRouter, ModelCache, authoritative licenses, RoutingPolicy, GenerationToken, provider clients]
outputs: [existing MatchRecord candidates, ConceptNote references, router manual queue]
permissions: [read frozen profiles, write data/eltiro, invoke only host-injected 806 providers through router]
data_sources: source-owned SeriesProfile metadata; only the current demand indicator for judgments
license: authoritative source registry; 806 enforces cloud and authorization restrictions
errors: ValueError for invalid/bounded input; RouteInvalid for changed context; RerunCacheMiss for missing original output; ProviderUnavailable without fallback; SQLite errors propagate
tests: [tests/eltiro/test_model_match.py, tests/eltiro/test_concept_notes.py, tests/regressions/test_audit_wisteria_807.py]
```

模型判断位于确定性召回、可选本地向量召回之后。`model=False` 是默认值，
不会读取模型配置、创建模型任务或生成概念说明，原匹配配置指纹保持不变。
`deterministic`、`vector`、`model` 分别控制各档；前两档至少开启一档，因为模型不能召回序列。
向量档原有的本地 Embedder 约束不变。

## Python 调用

```python
result = match_demand(
    demand, generation, profiles=profiles, consumer=consumer,
    observations=observations, catalog=catalog, chunks=chunks, store=matches,
    deterministic=True, vector=False, model=True,
    router=router, cache=cache, licenses=current_registry_licenses,
    policy=policy, generation_token=token, providers=providers,
)
```

`router` / `cache` 可使用 806 的 `SQLiteModelRouter` / `SQLiteModelCache`；
`providers` 是宿主注入的提供方客户端映射。本模块不发现模型、不读取凭证、不直接调用提供方。
`consumer` 来自可信使用方配置；`licenses` 必须由当前权威来源注册表提供，不能使用画像里的许可副本。
`generation_token` 必须属于本次代次，三个读取视图也必须由调用方固定。
所有新参数均为关键字参数；既有调用方无需修改。此任务不提供命令行。

也可直接调用：

```python
judged = judge_candidates(
    demand, generation, existing_records, profiles=candidate_profiles,
    consumer=consumer, data_root=data_root,
    router=router, cache=cache, licenses=current_registry_licenses,
    policy=policy, generation_token=token, providers=providers,
    concept_notes=notes,
)
notes = generate_concept_notes(
    generation, demand=demand, profiles=profile_store, consumer=consumer,
    data_root=data_root, router=router, cache=cache,
    licenses=current_registry_licenses, policy=policy,
    generation_token=token, providers=providers,
)
notes = list_concept_notes(generation, data_root=data_root, consumer=consumer.id)
```

`judge_candidates` 的 `profiles` 是画像序列，`generate_concept_notes` 的 `profiles` 是 `ProfileStore`。
前者返回候选记录列表而不保存匹配；`match_demand` 才原子保存完整结果。
`list_concept_notes` 用于内部人工参考，省略 consumer 可供宿主管理员查看全部；不能直接作为来源说明输出。

## 输入、难度与许可拆分

每个 pending 候选构造一个单来源 `ModelInput`。只包含当前指标的 concept、unit、
正反例，以及该候选的完整画像：概念与名称、单位、口径、调查、地区、时间、来源及完整序列身份。
不包含需求 purpose、source_case、运算、其他指标、其他需求或其他使用方的内容。
模型概念说明只有在代次 token、完整序列身份、画像内容哈希、使用方和授权来源范围均匹配时加入，
字段名为 `model_generated_concept_notes`。说明不参与召回。

先按候选拆分再路由，所以混合来源不会共享提示词，也不会把受限候选作为云端候选的背景。
难度由当前指标候选确定，规则依次为：

1. 另一个 pending 候选的置信度差不超过 0.05，或画像缺少 methodology、unit、survey 任一项：high。
2. 信息完整且三项兼容性都是 same：low。
3. 其余：medium。

概念说明难度为 medium。806 从请求难度向下降档，同时约束使用方上限、需求预算、
来源许可、提供方配置及授权。`no_outflow` 即使 cloud_model_ok=true 也不能进云端；
cloud_model_ok=false 只能本地或 none。缺许可也走人工队列。
一次 Python 操作对应一个冻结模型 run：分别为 match_judge 和 concept_note，
各自使用需求的 max_calls_per_run 并由 806 再收紧到使用方运行预算；缓存命中不耗调用次数。

## 固定输出与记录

通过 806 的 `ModelResult.fields.answer.value` 接收纯 JSON，判断输出必须恰好为：

```json
{"verdict":"relevant","reason":"一句理由。"}
```

verdict 只接受 relevant / irrelevant / uncertain，reason 最多 512 字符、单行单句。
重复键、多余字段、缺字段、错误类型、无效 JSON、非 answer 模型字段均按 uncertain 处理，
不重试、不换模型。置信度分别为 0.9 / 0.1 / 0.5，是排序分而非概率。

有效调用（包括格式无效而被归为 uncertain 的调用）生成 tier=model 记录。
`MatchRecord` 契约没有独立的 ModelUse 属性，因此 `reason` 存完整 JSON：
label="模型生成"、verdict、reason、model_use={provider, model, version, tier}，
以及 matcher_reason（原召回理由）。无需改变模型或契约。
序列身份、出处、口径兼容性和人工状态全部保留；模型档始终 auto_bind=false。
模型判为 irrelevant 也保留 pending，交由人决定。

confirmed / rejected 均不送模型，不改人的决定。none 或预算耗尽保持原候选档位、
理由和分数，清除自动绑定并进入人工队列；其路由原因保存在 806 人工队列中。
模型只能调整现有 pending 候选分数和排序，不能引入新序列、合并身份或改变兼容性。

## 概念说明与存储

只有 descriptions 为空，或 descriptions 被标为模型生成（没有来源自带说明）的画像才生成说明。
输出恰好为 `{"note":"一句概念说明。"}`，同样受单句和 512 字符限制；格式无效时不保存说明。
none 时不生成。生成函数不写回 ProfileStore。

`<data>/eltiro/concept_notes.sqlite` 按 generation、完整序列身份、画像内容哈希、
806 缓存键摘要保存。画像哈希仅排除 generation，缓存键保留完整 token，
并保存 ModelUse 和 label="模型生成"。`ConceptNote` 返回这些字段，text 仅供模型输入与人工参考。
确定性档和向量档均不读取说明。

`<data>/eltiro/model_tasks.sqlite` 在调用前保存 806 的冻结 plan，以及预算耗尽等人工结果。
它不是第二套路由或模型输出缓存；模型输出仍只由注入的 806 cache 保存。
两类数据库都仅供 Eltiro 内部使用，随代次保留用于重放。宿主清理时应整体归档该代次的
任务、路由、输出缓存、说明和匹配结果；不能只删输出缓存再要求同一运行重新生成。
不自动清理、不复制 raw 快照。

## 重跑、重匹配与边界

同一使用方 / 需求版本 / 代次 / 步骤的 run id 稳定。重跑先让 806 核对原 plan 上下文，
再用 rerun=True 验证原模型输出。即使 matches 已保存，也不会跳过这一步。
许可或授权改变会失败；原输出丢失抛 RerunCacheMiss，不调用其他模型补上。
预算耗尽项单独保存人工结果，不会误当作缺失模型输出。
调用中断或提供方失败之后，原结果没有进入缓存的项也按缺缓存失败，不发布部分匹配。

`refresh` 透传所有新参数。模型档关闭时保留 808 的选择性重匹配和沿用算法。
开启时，新 generation_token 的缓存身份不同，不能复制旧代次的模型结论：
每个活跃需求重新执行候选匹配，再由 806 复用本代次的精确缓存。
这牺牲模型模式的跨代次跳过优化，保证与本代次全量逐记录一致；
对已完成代次的 refresh 重试也验证模型缓存。

保留 001 / 806 的输入上限：每个 ModelInput 的 content 最多 4096 字符、每个计划最多 256 项。
超限校验失败，不截断画像或省略出处，不拆成可绕过需求预算的多轮调用。
SQLite、提供方或校验失败均传播；模型调用没有本层重试或 fallback。
本任务的验证全部离线，真实模型对现有 806 提示词的遵循程度仍需人工抽查。
