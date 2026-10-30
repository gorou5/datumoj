# Eltiro 离线评测 v1

四个需求来自计划 6.8；`three-gdp-reconciliation`（#002）和
`greece-deficit-revision-chain`（V07）与只读样例逐字相同。
V13 的期间、事业所范围和 #003 的国家对、期间是本任务起草的评测范围，需人审。
不访问案例网站或官方网络，不把案例标题里的“100 / 60”当原值。

**当前资料不能证明真实端到端达标。** 所有标准匹配条目和数值点均为
`pending_h2`，不计入门槛。仓库只有概念样例，没有这些试点的原始观测或完整
接入身份。官方代码/链接是逐条核验入口，不表示本次已访问官方页面。
`values/` 的 `source_value: null, checked_at: null` 是待补证据，绝非缺值观测。
没有填造官方数字或核对日期；相应报告 `passed` 为 false，空集不能过检查点。

## 标准答案与 H2

每条 `gold` 记录含唯一 id、指标、完整形状的 `SeriesIdentity`、正负例、依据和状态。
`indicator_code: null` 表示未证实代码；带 unresolved 的表名和语义维度是核验描述，
不能当接入的正式 key。H2 要以实际 profile 的全部维度及标签替换它们再改为
`verified`。一个指标可有多个来源的正例，每条分别计入召回分母。

| 需求 | 官方依据入口 | H2 必须确认 |
| --- | --- | --- |
| V13 工资 | 厚劳省毎月勤労統計入口；ILO wages；OECD earnings | e-Stat statInfId、现金工资/5人以上/调查产业计、重算前后发布；ILO/OECD 是否真有等价月频序列 |
| #002 三个 GDP | WDI NY.GDP.MKTP.CD/CN；WEO NGDPD/NGDP；OECD National accounts | 来源注册 id、SDMX dataflow/维度、当前价格/币种/倍率；WEO 实绩预测边界 |
| V07 赤字修订 | 样例 gov_10dd_edpt1；B9/GD、S13；WEO GGXCNL_NGDP | 元数据维度、MIO_EUR 倍率、2009—2010 历次 EDP 存档及 IMF 历史版本、符号 |
| #003 镜像 | UN Comtrade 参数/primaryValue；WTO merchandise | 双边覆盖、分类版本、reporter/partner/flow、进口估价、customs/mode/partner2、权限与倍率 |

`intl-imf-weo`、`intl-oecd`、`intl-wdi`、`eu-eurostat` 沿用计划/样例；
`intl-ilo`、`intl-un-comtrade`、`intl-wto` 是待 H2 确认的暂定 id，计划只有来源名。
不能假定 WTO 有等价双边序列，不能假定 ILO/OECD 工资有同样调查范围。
不存在的候选要从正例删除并记录缺口，不通过放松口径让它成立。

## 匹配计算

```python
from pathlib import Path
from datumoj.eltiro.eval import load_gold, load_thresholds, score_matches

root = Path("eval/eltiro")
gold = [load_gold(path) for path in sorted((root / "gold").glob("*.yaml"))]
report = score_matches(gold, records, load_thresholds(root / "thresholds.yaml"))
```

`records` 是一个 generation 的 `MatchRecord` 集合，需求 id/version 必须属于 gold。
预测定义为未被人工拒绝的推荐，包含未自动绑定的候选；只输出大量候选会降低查准。
身份按 `(demand_id, demand_version, indicator_key, source_id, table_key, indicator,
indicator_code, sorted(fixed_dimensions))` 精确比较，不忽略标签、额外维度或版本。
调用者应先将 803 输出与标准答案统一到真实契约身份，评分器不做模糊匹配。
完全相同的重复预测只计一次；冲突重复、混合 generation、未知需求/version 抛 ValueError。

- TP：预测命中 verified 正例；FP：预测未命中正例（含 verified 反例和未列出的身份）。
- FN：verified 正例未被预测。查准 = TP / (TP + FP)，查全 = TP / (TP + FN)。
- 精确命中 pending_h2 身份的预测跳过；不把该来源所有其他猜测都豁免。
- overall 计算全部档位；deterministic 只选该档预测，查全分母仍是所有 verified 正例。
- 两档均要求查准 ≥ 0.95、查全 ≥ 0.8；verified 反例的 auto_bind 数必须为 0。
- 无预测时查准为 null；无正例时查全为 null、evaluated=false，不算达标。
- 报告列出漏配、错配及反例绑定数；允许门槛内漏配时仍保留逐条诊断。

当前 skipped_gold 是全部条目；checkpoint 902–904 应同时展示有效分母和 pending 列表。
不得仅用合成测试的通过结果宣称真实试点通过。

## 数值比较

`load_values(path)` 读取 ValueTable；`check_values(tables, rows)` 接收调用方从
CSV/Parquet 解码的行字典，不读任意输出包路径，不实现执行器或变换链。
行必须有 selector 指定的字段，以及 `unit`、`scale`、`value`，缺失值还需 `note`。
`period` 为字符串（如 `2018-01`）；发布 id 必须精确，不能把最新值代替历史发布。
#003 还按 reporter、partner 区分。输出应保留来源原始指标列供核对；派生的差额
不能冒充原值。如果还保留其他固定维度，在 selector 中补齐。

只与同来源、同发布的原值比较，绝不以另一个来源或来源中位数校验。
以 Decimal 计算 `输出值 × output_multiplier` 与 `source_value × source_multiplier`。
行中 scale 必须等于预期 output_multiplier，单位也必须相同；不自动猜测汇率或币种。
默认 absolute_tolerance=0，完全相等；若官方数值有效数字有限，在点上显式写
significant_digits，统一倍率后以 ROUND_HALF_EVEN 按有效数字舍入双方再比较。
非零容差按单货币单位计，必须在 evidence.note 说明理由。
缺值必须仍为 null 且 note 逐字相同，不能补零；不接受 NaN、Infinity 或布尔数值。
找不到行、匹配多行、错单位/倍率均失败。未覆盖的其他行不属于本次抽样检查。
只有取得官方原值、原始页/样本定位、发布和核对日期后才将点标 verified。

## 许可反例适配

`load_license_cases(path)` 读取合成反例；`check_license_cases(cases, gate)` 对每条调用
`gate(case) -> bool`，True 表示允许，False 表示拒绝。805 负责将 case 适配到实际闸门，
并从 case.inputs 的 registry_outflow 构造受信的注册表输入，不能使用 consumer_outflow
覆盖它。全部是必须拒绝的反例；全放行全部失败、全拒绝全部通过。
这仅验证拒绝行为，不证明闸门能正确放行合法请求。异常或非 bool 返回值也失败，
不把闸门崩溃当作正确拒绝。输入文字是数据，无指令执行、网络或真实许可变更。

接口是同步本地调用，无认证或网络超时；错误文件/非法记录抛 ValueError，业务失败
返回 MatchReport/CheckReport，带逐条 id 和原因。报告支持 Pydantic model_dump，
包含 passed。依赖仅使用已有锁定依赖。
