# Eltiro 匹配（803）

```yaml
id: eltiro-matching
name: Eltiro deterministic matching and local vector recall
version: 1
status: maintained
runtime: Python with standard-library sqlite3 and existing project dependencies
inputs: [registered DemandSpec, generation, ProfileStore, ConsumerConfig, frozen Rikolto read views, optional local Embedder]
outputs: [MatchResult, persisted MatchRecord, manual confirmation queue]
permissions: [read caller-frozen Rikolto views, write data/eltiro]
data_sources: Rikolto observations, catalog metadata, document chunks
license: inherited from sources; no_outflow is permitted for internal matching
errors: ValueError for validation/configuration conflicts; storage and embedding failures propagate
tests: [tests/eltiro/test_matching.py, tests/eltiro/test_match_store.py, tests/regressions/test_audit_wisteria_803.py]
```

## Python 接口与代次边界

```python
from datumoj.eltiro.match_store import SQLiteMatchStore
from datumoj.eltiro.matching import match_demand

with SQLiteMatchStore(data_root) as matches:
    result = match_demand(
        registered_demand, generation,
        profiles=profiles, consumer=trusted_consumer,
        observations=frozen_observations, catalog=frozen_catalog, chunks=frozen_chunks,
        store=matches, embedder=local_embedder,
        deterministic=True, vector=True, k=10,
    )
```

调用方负责选择已发布代次，并像 802 一样将三个 Rikolto 读取视图固定到该代次；
此接口不读取状态库、不获取最新代次。查询返回的 generation_token 如存在，其代次必须相符。
旧式视图没有 token 时，固定视图的责任仍属于调用方。804 负责生产接线。
需求须已登记，使用方配置须来自可信宿主；函数重新校验模型并核对 consumer 身份。
不接收目录或来源正文里的执行指令，不调用生成模型。

只从 allowed_sources 取候选，preferred_sources 只加分，不过滤。
不按许可等级过滤候选：no_outflow 也可用于内部匹配；805 负责输出许可闸门。
完整 SeriesIdentity（包括所有 fixed_dimensions）作为主键，不按同名、单位、调查、口径、
发布或 keep_separate_by 合并序列。源端未提供身份区别时，不自行推导新身份。

deterministic 与 vector 分别开关；同时为 False 抛 ValueError。
不传 embedder 时跳过向量档，result.vector_skipped 为 True（vector 开启时），不隐式连接任何服务。
只开向量档却没传 embedder 时仍返回跳过说明，可以是空结果。
所有读取为同步调用，无匹配层重试；查询超时沿用 ObservationStore 的默认超时。

## 确定性算法与置信度

指标输入文本按概念说明、正例顺序用换行连接；画像文本按 names、descriptions 顺序，
每组按语言键排序后用换行连接。代码比较保持原始大小写。
提取完整 ASCII 记号：包含数字或 `._:/-` 分隔符，或至少两个字母的全大写记号。
只按完整记号与 indicator_code 或 concept_ids.code 精确比较，拒绝子串命中；
句尾句号不算代码的一部分。被标注 model_generated_fields 的 concept_ids 不作为官方代码依据。

关键词使用 NFKC、casefold、去首尾空白后的 Unicode 词；连续中日文额外加入相邻双字词。
令 Q 为指标关键词集合、P 为画像关键词集合，交集非空即可召回。

| 命中方式（按优先级取一个） | 基础置信度 |
|---|---|
| indicator_code 精确命中 | 0.95 |
| 官方 concept_ids.code 精确命中 | 0.85 |
| 关键词命中 | 0.40 + 0.40 × len(Q ∩ P) / len(Q) |
| 向量召回 | 0.80 × clamp(cosine, 0, 1) |
| 仅沿用已有人工决定，无当前召回命中 | 0.00 |

若来源在该指标 preferred_sources 内，加 0.02。最终取 min(1, 分数)，四舍五入到六位小数。
分数是确定性的排序分，不是概率估计。确定性命中优先于同序列的向量命中，只保存一条记录。
排序依次是 confirmed、pending、rejected，其次 indicator_key、置信度降序、规范完整身份 JSON。

反例按 NFKC/casefold 后的完整短语子串，或其中代码与画像代码精确比较。
反例搜索包括画像名称、说明、代码、口径和调查；记录理由中的 `counterexample #N` 指向
已登记需求的第 N 条反例（从 1 开始），避免复制可能很长的原文。
反例只阻止自动绑定，不删除候选。

## 兼容性与绑定

unit 仅当两边均提供单位时判定：去首尾空白并忽略大小写，相等为 same，否则 different；
缺任一边则 unknown。methodology、survey 仅在官方代码精确命中时为 same，理由是代码限定了
口径和调查；其余为 unknown，不从名称相似或向量分数推断。

auto_bind 同时要求：确定性档命中、官方代码精确命中、三项兼容性均为 same、无反例、
未被人工否决。自动绑定保留 confirmation=pending，但不进人工队列；confirmation 仅表示人工决定。
向量候选绝不自动绑定。人工 confirmed 不会将不兼容候选改成自动绑定。

## 依据

每条候选选取一条排序稳定的真实依据，profile_field 为 series：

- 观测值：按 source_id、table_key 查询，完整匹配 indicator、indicator_code 和 dims，
  已有画像的 survey/unit 也必须与观测记录相符；直接取其 source_id、snapshot_sha256、locator。
- 目录：读取 802 定义的 `catalog:<dataset_key>`，必须有同来源的快照哈希及 locator。
- 文档：读取 802 定义的 `document:<kind>`，从同来源、同类型的文档块取依据，并核对文档快照。

取不到依据的已召回序列不生成记录，去重加入 result.missing_evidence；不从画像臆造哈希。
这是候选的出处索引，不替代对需求概念是否正确的人工判断。

## 私有向量缓存与匹配持久化

向量写在 `<data>/eltiro/vectors/generation-N/<模型身份摘要>.sqlite`。
模型身份包含代次、模型名、维度；缓存打开后仍检查存储的完整身份，拒绝混用。
文本以内容哈希索引，不缓存原文；只嵌入允许来源的画像文本及需求查询文本。
验证返回条数、维度和有限数值，归一化后按余弦分数取每指标前 k 个，1 ≤ k ≤ 50。
并列按完整身份 JSON 排序。失败传播，不发布部分匹配结果。

Embedder 是可信宿主注入的本地实现；契约仅提供 model、dim、embed，没有可审计的网络位置字段，
所以宿主必须保证其本地性，本模块不接受云端提供方配置，也不构造嵌入客户端。
测试使用 Rikolto 的 HashEmbedder，不连接 Ollama。不同模型可使用各自缓存文件；
同一已完成匹配运行不能切换模型名或维度。

SQLiteMatchStore 实现 001 的 MatchStore，路径为 `<data>/eltiro/matches.sqlite`。
put 保留插入顺序与人工状态；set_confirmation 是修改已有人工状态的接口，否决清除 auto_bind。
save_result 在一个 SQLite 事务里保存整份需求一个代次的全部匹配及缺依据/跳过说明。
数据库故障回滚整次结果；向量缓存与匹配结果分属独立库，失败时可能留下可复用的向量缓存。

同需求版本、同代次重跑读取已保存结果（包括空结果），不重新读取 Rikolto 或调用嵌入。
需求、使用方配置、档位开关、k、嵌入模型身份有变化时拒绝覆盖已完成代次结果。
这也避免授权收紧后从旧结果中返回不再允许的来源。
供其他模块显式调用的契约级 put 仍可更新记录；它不是一次完整匹配运行的替代品。

新代次读取同需求版本的历史决定，每个完整身份使用最近旧代次的状态；confirmed 排前，
rejected 留作审计但从 recommendations 排除。需求版本变化不沿用，因为概念可能变了。
序列已消失则不复制；仍存在但无法取依据则列为缺依据。人工决定也可在没有当前关键词或向量
命中的情况下沿用，但仍需当前代次的画像和依据。
记录及缓存保留至宿主显式清理，无自动清理或迁移；不能删除仍需审计或重跑的代次文件。

## 确认队列 CLI

```sh
eltiro matches queue --consumer example-consumer --demand example-demand
eltiro matches confirm example-demand --version 1 --indicator gdp --series '{"source_id":"intl-wdi","table_key":"gdp","indicator":"Nominal GDP","indicator_code":"NY.GDP.MKTP.CD","fixed_dimensions":{}}' --generation 1
eltiro matches reject example-demand --version 1 --indicator gdp --series '{"source_id":"intl-wdi","table_key":"gdp","indicator":"Nominal GDP","indicator_code":"NY.GDP.MKTP.CD","fixed_dimensions":{}}' --generation 1
```

queue 输出 JSON 数组：已登记需求各代次中 pending 且非自动绑定的候选，两个筛选参数均可省略。
confirm/reject 按完整 JSON 序列身份更新特定代次，成功输出确认状态。
操作错误使用固定错误信息，不输出需求原文；本机宿主配置与状态目录须由操作者控制，
沿用 801 的目录校验并拒绝数据库及 journal 文件符号链接/硬链接。
本任务不增加启动匹配命令。
