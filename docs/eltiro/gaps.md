# Eltiro 缺口报告

`datumoj.eltiro.gaps` 提供同步、离线的 Python 接口。宿主传入需求、当前授权配置，以及指定代次的匹配库和画像库；模块只调用契约读接口，不重新匹配，不读取执行结果，不调用模型或 Rikolto 写接口。

```yaml
id: eltiro.gaps
name: Eltiro gap reports
version: 1
status: maintained
runtime: Python >=3.12,<3.14
inputs: [DemandSpec, Generation, ConsumerConfig, MatchStore, ProfileStore, Registry, DemandRegistry]
outputs: [eltiro.gaps/v1, eltiro.gaps.aggregate/v1, host-markdown]
permissions: [read-matches, read-profiles, read-source-registry, read-active-demands, write-eltiro-reports]
data_sources: caller-supplied-stores-and-demand-preferences
license: unknown
errors: [ValueError, propagated-storage-errors, propagated-io-errors]
tests: [tests/eltiro/test_gaps.py, tests/regressions/test_audit_wisteria_809.py]
```

## 接口与文件

```python
from datumoj.eltiro.gaps import aggregate_gaps, gap_report

report = gap_report(
    demand, generation,
    consumer=consumer_config, matches=match_store, profiles=profile_store,
    registry=source_registry, data_dir=data_dir,
)
host_report = aggregate_gaps(
    generation,
    demands=demand_registry, consumers={consumer_config.id: consumer_config},
    matches=match_store, profiles=profile_store, registry=source_registry, data_dir=data_dir,
)
```

两者返回独立的、可 JSON 序列化的 `dict`。库实例由宿主管理生命周期；接口不读凭据、不联网，不涉及超时和认证。宿主必须提供稳定的代次视图，并传入当前完整的使用方配置映射。公开格式版本由 `schema` 字段标识。

单份报告写到 `<data>/eltiro/gaps/<demand_id>-v<version>-g<generation>.json`，包括需求 id、版本、代次，以及每个指标的 `key`、`concept`、`min_sources`、`bound_sources`、`gaps`、`possible_sources`。无缺口指标也保留，列表为空。报告不复制需求原文、目的、样例、匹配理由、画像正文或其他需求的信息。

汇总仅调用需求注册表的 `list_active()`，按来源 `by_source` 与指标概念 `by_concept` 分组，写到 `<data>/eltiro/reports/gaps-g<generation>.json` 和 `.md`。概念按文字完全相等分组，不推断同义关系；同一来源/概念下保留需求、版本、使用方、指标和缺口，方便宿主回溯。Markdown 用 JSON 代码块展示分组，转义反引号和 HTML，避免把概念文本解释为指令或标记。没有缺口的指标不进入汇总；没有在用需求时写出空分组。

汇总不调用单份报告的写文件入口，不写任何使用方的发件目录。目的目录逃出 data 或落入已知使用方 outbox（包括符号链接重定向）会报错。单份接口只知道当前使用方的配置，其固定 `eltiro/gaps` 目录应由宿主管理，不能配置成其他使用方的发件目录。

## 缺口判定

候选限定为本需求 id、版本、代次、指标和当前 `allowed_sources` 内的匹配记录。画像按完整序列身份读取，包含所有固定维度；不从匹配置信度、理由或文件名猜测可得程度。

| 种类 | 判定 |
| --- | --- |
| `missing` | 没有授权范围内的候选；全部否决仍属于有候选 |
| `catalog_only` | 有目录候选，且所有候选都没有 `observations` 画像 |
| `document_only` | 有文档候选，且所有候选都没有 `observations` 画像 |
| `pending_confirmation` | 有待确认观测候选，且没有任何观测候选被确认或自动绑定 |
| `below_min_sources` | 已确认或自动绑定的观测候选，其不同 `source_id` 数量小于 `min_sources` |
| `rejected_only` | 非空候选集合全部被人否决 |

需求所列五类中，可得程度一类有 `catalog_only`、`document_only` 两个代码。规则独立叠加，例如没有候选同时是 `missing` 和 `below_min_sources`；目录与文档混合时两种代码都出现；全部否决不会被当成待确认。仅目录/文档的确认或自动绑定不提供观测来源数。同一来源的多个表、发布或固定维度仍只算一个来源。已拒绝的观测不计入绑定。

这是匹配覆盖层面的报告，不保证国家、年份、修订版逐点覆盖，也不判断倍率、地区码和输出运算是否能执行。执行层缺口由 804 记录，805 以后合并。

## 可能来源及隔离

| 原因 | 依据 | 可见范围 |
| --- | --- | --- |
| `catalog_only` / `document_only` | 该指标匹配候选的画像可得程度 | 本需求和宿主 |
| `preferred_unregistered` | 本指标自己列出的偏好 id 不在当前来源注册表 | 本需求和宿主 |
| `registered_not_allowed` | 注册表里存在、但不在该使用方 `allowed_sources` 的 id | 仅宿主 |

未注册的偏好可以是尚待接入的来源，即使当前授权配置没有列出，也能原样作为该需求自己提出的线索；已注册但未授权的偏好不会进入单份报告。来源 id 去重，原因合并。被否决的目录/文档候选仍能作为已有来源线索出现，同时明确保留 `rejected_only` 缺口。

宿主授权范围外的注册来源并没有已验证的指标相关性：汇总明确标为相关性未知，不能解释为已经发现可补足的观测。这里不输出来源地址、凭据、注册表描述或其他使用方信息，也不会生成注册表和需求没有给出的来源。只有有缺口的指标才附可能来源。

## 错误与存储

需求、代次和配置在入口重新校验；使用方不匹配、缺少在用需求的配置、匹配序列画像丢失或代次/身份不一致都会报 `ValueError`，不把损坏视图伪装成正常缺口。汇总先完成全部读取和计算，再发布文件。

每个文件使用同目录临时文件、flush、fsync、`os.replace` 原子替换；失败清理临时文件并向调用者传播错误。单份旧文件在替换前保持完整。JSON/Markdown 各自原子，不提供两个文件一起提交的事务；中间失败时重跑可重新生成一致结果。固定命名会覆盖该需求/代次的旧报告，以反映后续人工确认和当前授权；其他版本、代次保留。文件由宿主按需清理，不自动归档或推送。

测试全部使用合成记录、假实现及 `tmp_path`；两个计划样例只读加载。回归测试使用仅允许契约读方法的代理，拒绝匹配库、画像库和注册表的写访问。没有新增模型、契约或命令行接口。
