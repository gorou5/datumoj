# JMA 来源连续多轮 BudgetExceeded 诊断

- 日期：2026-11-05
- 基线：`main` @ `fdade72`
- 范围：只读诊断加复现脚本，代码改动在 `fix/jma-budget-timer`（补丁草案，未合并，未开 PR）
- 没碰：e-Gov、BODIK 相关代码
- 没拿到：真实运行的数据（`runs.jsonl`、云端 `manifest.sqlite`）。下文"已证实"指已由代码和离线复现证实；涉及线上的部分标为推断，并给出用真实数据核对的方法

## 结论

**"连续多轮"报错，主因是一次失败被永久保留，之后每轮又把它算成本轮失败。不需要每轮都超时。** 置信度：**高**（有代码路径，有离线复现）。

1. `budget_exceeded:*` 一旦记成解析失败，在同一个 parse context fingerprint 下**永远不重试**。但每次 `parse_pending` 都把它重新放进 `summary.failed`。云端 `_source` 再把它算成 `parse_index_failed`，于是这次运行 `status=failed, exit_status=1`。monitor 看到连续 3 次失败，就报 `consecutive_failures`。
   这样只要第一次超时发生过一次，此后 jp-jma 每轮都是"失败"。解析器不再运行，也不再读 raw。
2. 预算"消耗"偏多（也就是第一次超时为什么会出现），最可能的原因是 **60 秒解析计时把 raw 读取也算了进去**。计时器在 `deps.raw.get()` **之前**就开始了。云端 raw 走 S3/R2（`read_timeout=60`，失败后最多重试 2 次）。一次慢的 GET 就足以用光 60 秒，而 JMA XML 本身解析只要几十到一百多毫秒。置信度：**中**（代码路径和复现都成立，但线上那一次是否真是慢读取，没有用真实数据核对）。
3. 两件事叠加的后果比"告警噪声"更重：失败的那条 JMA 条目**永久缺失**。爬虫认为它已经保存成功，不会再抓；解析器又永不重试。JMA 长期 feed 只保留约 7 天，过了这段时间就补不回来。

## 1. 预算在哪里计量、在哪里消耗

| 位置（main） | 作用 |
|---|---|
| `src/datumoj/rikolto/core/budget.py:15` | `DEFAULT_MAX_PARSE_SECONDS = 60.0` |
| `core/budget.py:97-99` | `check_time()`：`clock() - started > max_seconds` 时抛 `BudgetExceeded("duration_seconds")` |
| `pipeline.py:368` | `_process` 里创建 `checker = BudgetChecker(budget)`，**计时从这里开始** |
| `pipeline.py:369` | `data = deps.raw.get(snap.sha256)`，**在计时窗口内** |
| `pipeline.py:374-378` | `parser.parse(...)` 后第一次 `check_time()`，之后每个 chunk 再查一次 |
| `parsers/jma_xml.py` | **JMA 解析器本身不读预算，也不计时**（只有 `pdf`、`gov-web-html-page` 会拿到预算 meta）。所以 JMA 只可能在 `pipeline.py:375/378` 那里超时 |
| `core/rawstore_s3.py:41-44` | 云端 raw：`connect_timeout=10, read_timeout=60, retries={"max_attempts": 2}` |
| `.github/workflows/cloud-run.yml:63` | 云端 `DATUMOJ_RAW_BACKEND: s3`，`restore(raw="remote")` 之后 `deps.raw` 是 `S3RawStore` |

所以对 JMA 来说，"60 秒解析预算"实际计的是：从 S3 读出这个 blob，算 SHA-256，读本地 snapshot meta，加上 lxml 解析。时间几乎全在第一项。

## 2. 历史失败如何逐轮重复计数

```
pipeline.py:151-156  parse_pending
    previous = manifest.get_parse(sha256, parser, version, fingerprint)
    if previous.failed and previous.error.startswith("budget_exceeded:"):
        summary.failed[snapshot_id] = previous.error      # 每轮都重新放进去
        continue                                           # 不重试，不调解析器，不读 raw
cloud.py:76-78       failed = sum(belongs(...) for ... in summary.failed)
                     record["failures"]["parse_index_failed"] = failed
cloud.py:92          status = "failed", exit_status = 1
monitor.py:351-364   最近 3 次运行都不成功 → consecutive_failures (error)
```

- 这是有意设计，不是笔误：`docs/tasks/DATUMOJ-MAPLE-110.md` 第 3 条要求超限"不可重试（同一解析器版本下不反复重试）"。`tests/core/test_budget.py` 也断言第二轮 `second.failed == first.failed`。
- 这个规则对 `pages`、`text_chars`、`output_chunks` 是对的：快照和预算不变，结果就是确定的。**对 `duration_seconds` 不对**：耗时取决于机器、网络和负载，同一快照下次很可能成功。缺陷在于把这项不确定的结果也当成了确定的。
- 有一个旁证特征：粘滞路径**不调用** `_record_parse_failure`，所以 change log 里这个快照只有 **1 条** `parse_failed` 事件，而运行台账里是每轮都失败。
- 爬虫一侧（`crawlers/jma_feed.py:92-99`）：`saved_ids` 按快照 `outcome ∈ {ok, not_modified}` 计算，不看解析结果。失败的条目被当作"已保存"，不会重新抓取。所以它不会自愈，只能永久缺失。

## 3. 为什么是 JMA

上面的机制对所有来源都成立。下面是 JMA 更容易先撞上的原因，属于**推断**：

- `schedule: hourly`，是频率最高的来源之一。每轮都有新快照，每个新快照都要在计时窗口内从 S3 读回来。
- 长期 feed `regular_l.xml` 实测约 **4.6 MB**（fixture）。feed 每小时都变，每轮都会产生一个新快照，由 `JmaXmlParser` "解析"（返回空结果），但 raw 照样要从 S3 读一遍。在所有来源里，这是每轮必读的较大 blob 之一。
- 所以在云端，JMA 每轮的 S3 读取次数和字节数都偏多，最容易碰上慢 GET 的长尾。

## 4. 已排除或证据不支持的解释

| 候选 | 判断 | 依据 |
|---|---|---|
| JMA XML 解析本身慢 | 排除（高） | 实测：4.6 MB feed 约 120-135 ms/次，最大条目 686 KB 约 40-75 ms/次，离 60 s 差三个数量级以上 |
| `pages`、`text_chars`、`output_chunks` 超限 | 基本排除（高） | JMA 每条最多 1 个 chunk；feed 返回 0 个 chunk；500 万字符的上限远高于条目大小。用户描述的也是 60 秒那一项 |
| 每轮都有新快照超时（系统性变慢） | 不能排除（需真实数据） | 如果 S3 一直很慢，新快照也会每轮超时。用下面的方法 1 区分：`parse_index_failed` 保持不变，说明是粘滞重复计数；逐轮增长，说明还有持续的新超时 |
| feed 的 `after_fetch` 读上一份 feed 太慢 | 与本问题无关 | 那是抓取阶段（`ctx.raw.get`），不在解析计时里；慢了只会影响抓取时限 |

## 5. 复现

脚本：`comms/repro_jma_budget.py`。离线运行，用真实 JMA fixture、真实 `JmaXmlParser`、真实 `parse_pending`、`cloud._source` 和 `monitor.evaluate_alerts`。为了不真等 60 秒，按比例缩小：`max_seconds=0.5` 代表 60 s，0.6 s 的 sleep 代表一次慢 S3 GET。

```bash
uv run --frozen python comms/repro_jma_budget.py
```

在 `main @ fdade72` 上的实际输出：

```
== A. JmaXmlParser 实测解析耗时（真实 fixture） ==
  regular_l.snapshot1.xml                    4586489 B     135.5 ms/次
  20261020112204_0_VPWP50_050000.xml          686457 B      41.4 ms/次

== B. 慢读取计入解析预算 ==
  第 1 轮 failed={'jma-entry-1': 'budget_exceeded:duration_seconds'}  parser 调用=1  raw.get=1

== C. 粘滞失败逐轮重复计数（之后 raw 已恢复快速） ==
  第 2 轮 cloud._source: status=failed exit=1 failures={'parse_index_failed': 1}  parser 调用累计=1  raw.get 累计=1
  第 3 轮 cloud._source: status=failed exit=1 failures={'parse_index_failed': 1}  parser 调用累计=1  raw.get 累计=1
  第 4 轮 cloud._source: status=failed exit=1 failures={'parse_index_failed': 1}  parser 调用累计=1  raw.get 累计=1
  monitor 提醒: ['consecutive_failures', 'overdue']
  change_log 中 parse_failed 事件数: 1（失败只真正发生过 1 次）
```

结果说明：解析器只跑过 1 次，raw 只读过 1 次，后面 3 轮全部失败并触发告警。

## 6. 用真实数据核对（建议做，能把第 2 条结论从"中"提到"高"）

1. **运行台账** `runs.jsonl`：看 jp-jma 各轮 `failures.parse_index_failed`。值不变（例如一直是 1），说明是粘滞重复计数；值逐轮增长，说明每轮还有新的超时，要重点查 S3 延迟。
2. **manifest.sqlite**：找出粘滞的快照：
   ```sql
   SELECT s.snapshot_id, s.resource_url, s.fetched_at, p.error, length(s.meta)
   FROM parsed p JOIN snapshots s ON s.sha256 = p.sha256
   WHERE s.source_id = 'jp-jma' AND p.status = 'failed' AND p.error LIKE 'budget_exceeded:%'
   ORDER BY s.fetched_at;
   ```
   - 看 `error` 是不是 `budget_exceeded:duration_seconds`。如果是别的项，第 2 条结论要重新评估。
   - 看 `resource_url` 是 feed（`regular_l.xml`）还是条目，以及首次出现的 `fetched_at`。
3. **change log**：同一 `snapshot_id` 的 `parse_failed` 事件应该只有 1 条，时间点就是"第一次超时"发生的轮次。拿这个时间去对 Actions 日志或 R2 的延迟。

## 7. 修复方案（补丁草案在 `fix/jma-budget-timer`）

两处改动，都在 `src/datumoj/rikolto/pipeline.py`，不碰解析器和其他来源的代码：

1. **计时器移到 raw 读取之后**（`_process`）：解析预算只计解析。I/O 的超时由 S3 客户端自己的 `read_timeout` 负责。这去掉了"慢读取耗尽解析预算"这条路径。
2. **`budget_exceeded:duration_seconds` 不再粘滞**（新增 `_sticky_budget_failure`）：确定性的 `pages`、`text_chars`、`output_chunks` 仍按 MAPLE-110 不重试。超时则在下一轮正常重试，计入 `retried`。
   - 线上已经粘住的 JMA 快照**会在下一轮自动重解析**，不需要迁移，也不改 fingerprint。如果改 fingerprint（比如给 jp-jma 加 `scope.parse_budget`），会让所有 JMA 快照重新解析，所以不推荐那样做。

测试结果见 §9。

### 需要人决定的点

- **第 2 处改动放宽了 MAPLE-110 第 3 条**（"超限不可重试"）。代价是：如果某个 PDF 或 HTML 在没有 I/O 的情况下每次都确实解析超过 60 s，它会每轮重试一次，每轮最多多花约 60 s。jp-jma 不受影响。要严格守住原规则，可以只合第 1 处，再手工处理已粘滞的记录。但只合第 1 处的话，**线上已粘住的快照不会自愈**，每轮仍会报失败。
- **补丁没改的部分**：确定性的预算失败（例如 `output_chunks`）每轮仍会被计入 `parse_index_failed`，让来源永久处于"失败"。这是现有测试明确要求的行为。是否要把"历史粘滞失败"和"本轮新失败"分开计数（比如台账里用单独的 `parse_policy_known` 计数，不让 exit_status 变成 1），属于监控语义的决定，这次没有动。

## 8. 不确定性与翻盘条件

- 如果 §6-2 查出 `error` 不是 `duration_seconds`，第 2 条结论不成立；第 1 条仍然成立，因为任何 `budget_exceeded:*` 都会被重复计数。
- 如果 §6-1 显示 `parse_index_failed` 逐轮增长，说明还有持续的慢读取或环境问题。第 1 处修复仍然有效（去掉了 I/O 计时），但还要另查 S3/R2 延迟。
- `belongs()` 只按 `snapshot.source_id` 过滤，不影响上面的结论（失败快照本来就属于 jp-jma）。

## 9. 测试结果（fix 分支）

分支 `fix/jma-budget-timer`（基于 `main @ fdade72`），改动只在 `src/datumoj/rikolto/pipeline.py`（+13/-2）和 `tests/core/test_budget.py`（+2 个测试）。

| 检查 | 结果 |
|---|---|
| 新测试在**未修复**的 main 代码上 | 2 failed（按预期失败：`test_slow_raw_read_does_not_consume_parse_duration_budget`、`test_duration_budget_failure_is_retried_on_a_later_round`） |
| `tests/core/test_budget.py` + `tests/regressions/test_audit_wisteria_104.py` | 24 passed |
| 全量 `uv run --frozen pytest -q` | **4617 passed**，1 warning（原有的 `os.fork` DeprecationWarning，与本改动无关），用时 11 分 31 秒 |
| 在 fix 分支上跑复现脚本 | B 段 `failed={}`；C 段第 2-4 轮全部 `status=completed exit=0`；monitor 无告警 |

原有测试 `test_pipeline_records_policy_failure_without_publishing_or_retrying`（确定性超限不重试，第二轮仍报失败）**没有改，仍然通过**。
