# Eltiro 固定代次执行器（804）

本实现有一处待裁决的契约冲突：`MovingAverage.window` 和
`LeadLagCorr.window` 允许最大 1200，但 `CitationEntry.inputs` 最多 256。
超过 256 个不同输入的运算会中止整次运行，不截断出处、不发布部分结果。
模型在本任务 scope 外；因此当前实现不能宣称满足完整的 1200 窗口范围。
复现见 `test_large_window_fails_without_dropping_required_citations`。

## 调用与存储边界

```
eltiro --data-dir data --sources-dir sources run <demand_id> [--version N] [--generation-token T]
eltiro --data-dir data runs list
eltiro --data-dir data runs show <run_id>
```

未给版本时选登记库当前激活版本；显式版本可运行已登记的历史版本。
`run_registered` 用登记库、使用方配置和来源注册表调用 `execute_demand`。
程序化调用者必须提供可信的 ConsumerConfig、Registry 和已登记 DemandSpec。
不创建模型客户端。运行命令同时完成生成画像和启动确定性匹配。

唯一 Rikolto 打开入口是 308 的 `open_generation_views`；不自行定位代次库，
也不打开在线库。未给 token 时由该入口内部调用 `pin_generation()` 一次。
画像、匹配和执行共用它提供的 observations/catalog/chunks 三个只读视图。
CheckedObservations 为每次 SQL（包括 describe、802 和 803 的 SQL）显式传入同一
完整 token，并核对返回 token。读前和发布结果前用空 SELECT 再校验。
无效 token、依赖文件被改动、唯一出处缺失、Decimal 类型不符等均停止，
不会尝试最新代次。错误出口不带 SQL、需求原文、来源正文或数值。

画像不存在时调用 802；大于 1 的代次传上一代编号做增量缓存读取。
调用 803 时关闭向量和模型路径。随后重新从匹配库读取人类决定；
仅 confirmed 或非 rejected 的 auto_bind 参与取数。pending 非自动绑定进缺口。
否决者不参与取数，来源授权仍在执行时校验。

内部数据保存到 `<data>/eltiro/runs/run-<随机标识>/`：

- `data.parquet`：`long/v1` 长表。每行一个 symbol 的原始或运算值；保存实体、周期、
  来源、完整序列身份、发布标识、发布日期、精确发布排序位置、单位、注记、citation_id。
  `source_spread` 的范围另存 Decimal `spread_range` 列。
- `citations.json`：每个 citation_id 的 CitationEntry，以及逐步说明（含换算来源依据、
  中位数和范围、缺值原因）。输入身份包括 source/table/obs/snapshot/release 全部五项。
- `gaps.json`：未绑定、待确认、频率或日历不符、地区不明、单位不符、倍率未确认、
  窗口缺失、分母为零等缺口，不用零填充。
- `record.json`：需求 id/版本、代次/token、起止 UTC 时间、实际绑定、行数和缺口数。

这是 805 的内部输入，保留原始指标与所有运算节点；`output.fields` 的选列、
宽表布局和 CSV 输出留给 805 打包，不向使用方发件目录写数据。
运行元数据使用 JSON 文件，与 Parquet、逐行出处一起通过同目录 rename 发布；
已有画像和匹配仍用各自的标准库 SQLite 存储。失败清理本次暂存目录，保留已有运行。
运行路径拒绝符号链接，run_id 只接受本实现的固定格式；不会按需求文本拼接文件名。
数值以精度 50 的 Decimal 运算，以精确公共 scale 的 Arrow decimal256 落盘；
超出 76 位精确表示能力时整次失败，绝不改成浮点或静默舍入。

## SQL、发布与身份

执行值只取 QueryResult.rows 的 Decimal/NULL，不从 provenance、画像、目录、文档取数。
SQL 过滤 source_id、table_key、indicator、indicator_code（包含 NULL 情形）、全部固定维度
及维度数量、需求实体和日期范围。维度用 JSON pointer 逐个比较，不受字典排列顺序影响。
字符串使用 SQL 字面量转义，不能注入语句。每行 row_evidence 必须恰好证明一个身份，
代次与五个身份列均须与 SQL 行一致；同一序列同一实体周期的多条引用视为歧义并中止。
不能靠 obs_id 跨表查找出处。

`latest` 默认 as_of_release=None，得到每个引用在固定代次内的最新发布。
`all` 对每个绑定的 source/table 调用同 token 的 `list_releases`；其结果已按
发布日期与 SnapshotOrder 排序。逐项把 **ReleaseEntry 对象原样**传给 as_of_release，
不折叠到日期或 release_id 字符串。因此同日的先后两次发布均能读到，同名的跨来源发布
也没有歧义。逐个引用（source/table/obs）比较相邻可见发布：值、注记、单位、实体、周期
均未改变则去重；改变后又恢复的值仍保留。引用未改变时不换成别条引用的出处。
来源没有 release_id 的兼容标识用 snapshot_sha256；released_at 仍保留 NULL，
不能把枚举接口兼容排序日当作真实发布日期，语义为 no_data_date。

revision_diff 即使在 latest 需求中，也额外枚举历史求首次基准；内部计算后仅保留每个
序列实体周期的最新位置。all 的结果保留每次改变和相对首次发布的差。

## 对齐

`areas.py` 收录 249 个 ISO 3166-1 已分配地区的 alpha-2/alpha-3/numeric 对照，
来源为 [ISO Online Browsing Platform](https://www.iso.org/obp/ui/#search/code/)。
只存代码，不把名称、来源 id 或注册表 country（来源所属国家）当观测地区。
来源 `SourceSpec.scope.area_code_scheme`、相应 Dataset.extras.area_code_scheme 可声明
`ISO3166-1-alpha2`、`ISO3166-1-alpha3`、`ISO3166-1-numeric` 或自有地区体系。
无声明时只有画像覆盖码全部精确落入同一 ISO 体系才接受；声明冲突、混合格式、
未知代码、模型生成地区范围都不猜。自有地区只接受需求中相同 scheme/code 的精确匹配。
数字码保留前导零，不补位，不接受 ISO 的口语别名；测试还核对三位码集合与契约一致。
这份内置表没有做本次在线重下载核验，未来 ISO 变更需显式更新与复核。

所有运算保留实体与同一频率周期。年为 1 月 1 日至 12 月 31 日，季/月为完整日历周期，
周为周一至周日，日为单日；财政年不冒充日历年。频率不符不升降频。
lag/window 使用日历偏移，缺一年不会拿上一条已出现的记录顶替。
同一序列的历史滞后值按当前发布位置取当时已可见的版本，不借用未来修订。
`release`/`release_id` 键要求发布标识和日期精确相同，`source` 键要求来源相同。
其余支持键：country、region、year、quarter、month、week、day、time、period、unit；
不支持的键显式失败。二元运算仅连接两个显式引用，在对齐组内保留每个来源组合。
来源 spread 组内同一来源出现多个序列则报歧义，不任意挑一个。

require_same_unit=true 时两个及以上输入单位未知或不同均给 NULL、记录 unit_mismatch；
不做汇率或数量级推测。已确认但不同的 scale 在没有显式统一时给 scale_mismatch。
rescale 以来源依据确认的画像 Scale 为唯一倍率，模型生成倍率不能算已确认。

## 运算定义与缺值

下表 x 为当前值，y 为显式右值，L 为 lag，W 为 window；增长率输出百分数，
ratio 与相关系数单位为 `1`。其他运算保留左侧单位。依赖图按拓扑顺序执行，允许前向引用。
每步保留所有实际输入的身份、上游运算链、参数和依据；不截断超过契约限额的出处。

| 运算 | 定义 |
|---|---|
| rescale | x × 来源 Scale.multiplier ÷ target_scale；倍率和原文依据进 steps |
| diff | minus: x − y；plus: x + y |
| diff_lag | x(t) − x(t−L) |
| ratio | x ÷ y |
| growth_rate | (x(t) ÷ x(t−L) − 1) × 100 |
| source_spread | 各来源 x − 本组中位数；偶数个值中位数取中间两数平均；另存 max−min |
| revision_diff | 同序列实体周期的本次发布值 − 首次发布值；不是减前次发布 |
| mirror_diff | 两个显式镜像引用的 left − right |
| residual | 实际 left − 显式基准 right |
| moving_average | 含当前周期的连续 W 期算术平均，不缩短窗口 |
| lead_lag_corr | W 组 (left(t−i), right(t−i−L)) 的 Pearson 相关；正 L 使 right 滞后；负 L 读取更晚周期 |

以上每个运算任何所需输入缺失都给 NULL，不删掉缺值后重算，不补零。
原始注记保留；缺日期或日历不完整记缺口。ratio/growth_rate 的零分母给
zero_denominator；相关任一边方差为零给 zero_variance；窗口不足给 incomplete_window。
倍率未知给 unconfirmed_scale。source_spread 少于两个来源给 insufficient_sources，
组内含 NULL 则整组给 NULL，不改用剩余来源。需求 min_sources 不足另记缺口。

## 验证

全部使用 tmp_path 合成数据和本地只读库；没有 HTTP 或模型调用。
回归审计复用运算、地区、运行存储用例，并检测固定代次、错误 token、身份歧义、
最终发布前失败、SQL 数值唯一来源、跨表同 obs_id、同日修订和两个样例需求。
测试结果、样例结果和尚未满足的契约范围见 `results/DATUMOJ-WISTERIA-804/summary.md`。
