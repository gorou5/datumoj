# Eltiro 增量重匹配与提醒（808）

```yaml
id: eltiro-rematch-alerts
name: Incremental demand matching and local evidence alerts
version: 1
status: maintained
runtime: Python with standard-library sqlite3 and existing project dependencies
modules: [datumoj.eltiro.rematch, datumoj.eltiro.alerts]
inputs: [active DemandRegistry, previous and new generation, ProfileStore, trusted ConsumerConfig mapping, frozen Rikolto read views, explicit as_of date, optional local Embedder]
outputs: [RefreshCounts, MatchResult in matches.sqlite, Alert in alerts.sqlite]
permissions: [read caller-frozen Rikolto views, write data/eltiro]
data_sources: Rikolto-backed profiles and evidenced match records
license: inherited from sources; internal use only
errors: ValueError for invalid generations/date/configuration; KeyError for missing consumer or alert; storage/read/embedding errors propagate
tests: [tests/eltiro/test_rematch.py, tests/eltiro/test_alerts.py, tests/regressions/test_audit_wisteria_808.py]
```

## Python 接口

```python
from datetime import date
from datumoj.eltiro.alerts import SQLiteAlertStore, list_alerts, ack_alert
from datumoj.eltiro.match_store import SQLiteMatchStore
from datumoj.eltiro.rematch import refresh

with SQLiteMatchStore(data_root) as matches, SQLiteAlertStore(data_root) as alerts:
    counts = refresh(
        new_generation, previous_generation,
        registry=registry, profiles=profiles, consumers=consumers_by_id,
        store=matches, alerts=alerts,
        observations=frozen_observations, catalog=frozen_catalog, chunks=frozen_chunks,
        as_of=date(2026, 3, 1), embedder=local_embedder,
        deterministic=True, vector=True, k=10,
    )
    pending = alerts.list_alerts(consumer="example-consumer", demand="example-demand")
    if pending:
        alerts.ack_alert(pending[0].alert_id)

# 也可单独打开库查询/确认；include_acked 默认 False。
pending = list_alerts(data_root, consumer="example-consumer", include_acked=True)
if pending:
    ack_alert(pending[0].alert_id, data_root=data_root)
```

同步接口，不做网络、推送、执行需求或写发件目录。宿主选择已发布代次，将三个读取视图
固定到新代次并保持画像不可变；本模块不读取发布状态或系统时钟。代次必须严格递增，
as_of 必须是 date。consumers 是宿主可信配置，不能从需求正文构造；缺配置直接报错。
每次 refresh 使用调用方给定的相同档位、k、嵌入模型设置，设置含义与 matching.md 相同。
所有 Rikolto 操作均为只读，同步查询沿用其默认超时，错误直接传播。

## 变化与反向索引

完整 SeriesIdentity 的规范 JSON 为主键，包括全部固定维度。画像内容做 SHA256，
只排除 generation 字段，避免同一内容进入新代次被误报为修改。新旧 key 的差集是新增/删除，
共同 key 的内容哈希不同为修改。删除和修改都检查旧画像，避免旧关键词已被改掉后漏选。

每次从 registry.list_active() 建内存反向索引，索引只含当前在用版本：

- 每个指标的 concept 和正例中的结构化官方代码、规范关键词；
- 指标 preferred_sources；allowed_sources 作为授权过滤，不拿偏好替代权限；
- 上一代次全部候选和缺依据序列的完整身份，保证已绑定序列变化必选；
- 当前需求版本所有旧代次非 pending 人工决定的完整身份。即使序列中间消失过，
  改名后重新出现也会重匹配；rejected 同样需要保留审计记录，不能只索引 confirmed。

向量档使用与 803 相同的文本、缓存、余弦和 top-k 排序比较新旧召回集合与分数。
803 没有固定正相似度门槛，召回边界是每指标前 k 个（包括零分和并列身份排序），
所以不能另外加正阈值来漏掉全量运行会返回的候选。删除 top-k 后的补位也会触发重匹配。
没有允许来源的候选增删或文本变化时，不计算向量；变化后仍低于 top-k 且不影响已记录候选时可以沿用。

另外检查上一代匹配的实际出处与缺依据状态，以及最新人工决定是否会改变新一轮匹配。
画像模型没有出处哈希字段，单靠画像哈希无法保证与全量运行的证据一致；因此这里需要只读核对。
人工确认后首次向新代次迁移时，803 会添加沿用决定的理由，也需要重匹配。
没有上代完整匹配结果、需求版本改变、可信配置或匹配设置改变时，执行首次完整匹配。

## 保存与重试

选中需求调用 match_demand。未选中需求不调用它，记录仅更新 generation，
其余字段、顺序、missing_evidence、vector_skipped 原样保存，使用同一配置指纹。
回归测试在独立的两个 SQLiteMatchStore 上保存相同历史和人工决定，逐条比较增量与全量运行，
包含顺序、理由、证据、缺依据和跳过标记，避免拿增量自己的缓存当全量证明。

RefreshCounts 包含 added、modified、deleted（序列数），rematched（本次调用匹配次数）、
carried（本次保存沿用结果次数）、alerts（新插入提醒数）。已完成的同代次结果重试时直接读库，
rematched/carried 不再计数。匹配和提醒各自使用事务；整批需求不是一个跨库事务，失败可能
留下已完成需求，重试会继续并补齐提醒。同一已完成新代次不允许切换配置，沿用 803 的冲突规则。

## 四种提醒

绑定指 confirmation=confirmed 或 auto_bind=True，且非 rejected。只比较画像与有出处的匹配记录，
不推测发布日，不调用模型。

| 种类 | 判定 |
|---|---|
| binding_stale | 上代已有绑定，同身份新旧画像 time_end 均已知，新值没有前进；as_of 严格超过新 time_end 的一个频率周期 |
| definition_changed | 上代已有绑定，同身份的新旧 unit、scale.multiplier、methodology、survey 任一改变；details 只列改变的字段名 |
| new_candidate | 新候选置信度高于旧绑定最高分，且此前未超过该分或自身分数进一步提高；或旧指标无绑定而现在首次有 auto_bind 候选 |
| gap_filled | 上代没有未否决候选，或这些候选全部为 catalog_only/document_only；新代出现 observations 的未否决候选 |

停更对 annual/quarterly/monthly 按日历加 12/3/1 月，短月截到月末；weekly/daily 加 7/1 日。
恰好一个周期不提醒；irregular、缺 time_end、日期已前进不提醒。序列删除本身不能证明停更
或口径变化，故不以删除推断这两种提醒。倍率只比较 multiplier，引用说明更新不算倍率变化。
可得性不明不能推断为缺口；取不到出处的序列不算有候选。相同分数不会触发更好候选提醒，
未变化的更好候选不在每个代次反复报为新候选。

只写入 demand.run.notify_on 列出的种类。提醒唯一身份由 consumer、需求 id/版本、指标 key、
完整序列身份、种类、代次生成哈希，INSERT OR IGNORE 去重。重试不会清除 ack。
数据库为 `<data>/eltiro/alerts.sqlite`，ack 未知 id 抛 KeyError，重复 ack 幂等。
查询可按 consumer/demand 组合筛选；宿主应在多使用方入口执行相应身份授权。

提醒字段不复制 purpose、source_case、concept、正反例、匹配理由或来源正文，
details 仅为固定规则/字段名；不包含其他使用方信息。完整序列身份是来源证据标识，
其中的官方指标名称/代码可能与需求中引用的官方名称/代码相同。
数据库目录须由可信宿主管理；保存至宿主显式清理，不新增自动清理或后台任务。
