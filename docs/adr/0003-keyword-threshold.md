# ADR 0003：关键词相关性门槛

- 状态：已采纳（2026-10-22）
- 相关：ADR 0001、DATUMOJ-TACHIBANA-501、DATUMOJ-IVY-903

## 背景

FTS5 trigram 索引不做日文分词。多词查询按 OR 搜索时，一个常见词即可带回内容无关的块；无空格长查询则需要衡量块覆盖了多少个查询片段。关键词侧因此需要覆盖率和 BM25 两个信号共同判定相关性。

## 决定

1. 查询按空白和 Unicode 标点拆分并去重。一个没有分隔符的长词拆成不重复的连续三元组；覆盖率是命中块覆盖的查询词或三元组占全部查询单元的比例。
2. 默认 `MIN_KEYWORD_COVERAGE = 0.5`、`MIN_KEYWORD_BM25 = 0.01`。仅当覆盖率低于 0.5 且 `-bm25` 低于 0.01 时才丢弃命中。没有 FTS BM25 分数的 LIKE 命中按 0 参加低分判断。`SqliteKeywordIndex` 的 `min_keyword_coverage`、`min_keyword_bm25` 参数可覆盖这两个值。
3. 混合检索沿用已有流程：关键词索引先返回通过相关性门槛的块，HybridRetriever 再按已有向量阈值剔除低分向量结果。两侧均没有有效命中时，现有缺口判定器返回 `zero_hits`。不调整向量阈值或 RRF。

## 校准

在离线 IVY 完整评测语料（83 条成功快照、60 道题）上运行 `tests/integration/test_full_eval.py`。先保存本任务改动前的基线，再比较不同覆盖率；BM25 下限保持 0.01。表中 law、catalog、stats 是分别按 `law.yaml`、`catalog.yaml`、`stats.yaml` 中适用题目的 Recall@10 均值计算；gap 列是 `behavior.yaml` 的缺口状态准确率。

| 配置 | 总体 Recall@10 | law | catalog | stats | behavior 缺口准确率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 改动前基线（coverage 无门槛） | 0.961538 | 1.000 | 1.000 | 0.900 | 1.000 |
| coverage 0.25，BM25 0.01 | 0.961538 | 1.000 | 1.000 | 0.900 | 1.000 |
| **coverage 0.5，BM25 0.01（采纳）** | **0.961538** | **1.000** | **1.000** | **0.900** | **1.000** |
| coverage 0.75，BM25 0.01 | 0.942308 | 1.000 | 1.000 | 0.900 | 1.000 |
| coverage 0.5，BM25 0.1 | 0.961538 | 1.000 | 1.000 | 0.900 | 1.000 |

coverage 0.75 使 `behavior-injection-citizen-notice-zh-window` 丢失预期命中，新增了正常问题召回损失；0.25 和 0.5 保持总体基线，采用其中更严格的 0.5。BM25 下限 0.1 在这批题上与 0.01 指标相同，因此保留较低的 0.01，减少边界相关命中的误删风险。

作为分数尺度检查，在离线关键词索引中，20 个块共同命中的常见词 BM25 为 `0.000001`；只在一个块出现的具体词 BM25 为 `2.667`。0.01 高于常见词分数，并低于该具体命中分数。该检查只用于解释下限的数量级，不替代 IVY 语料 Recall@10 对比。

## 结果与边界

- `behavior.yaml` 中已入库来源无匹配内容的题仍全部判为 `zero_hits`；全部缺口状态准确率为 1.0。
- law、catalog、stats 的 Recall@10 均不低于 0.85，且均未低于改动前值。
- `jp-estat-files` 的来源级 Recall@10 仍为 0.0：现有 e-Stat 解析器只写入观测值，不生成可检索块。这是改动前已有的评测边界；`stats.yaml` 聚合 Recall@10 为 0.900，Dashboard 来源为 1.000。本任务不改解析器或题目。
- 两道已有失败题保持不变：`behavior-time-estat-period-publication`、`stat-estat-tokyo-ict-employees-2016`。
