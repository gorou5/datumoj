# 验收记录：KATABAMI（R2 容量治理）检查点 901

- 日期：2026-11-18（UTC）记录
- 执行：主对话；计划 `docs/plans/katabami-r2-capacity.md` 第 6 节 901，按 `docs/plans/parallel-versions.md` 第 3.1 节第 5 条分两步
- 结论：**第一步（离线验收）通过，待第二步。**本 PR 合进 `main` 后，新的删除行为默认关闭；第二步由人从 `main` 手动打开 `R2_DB_RETENTION` 跑一轮，再盘点，结果补进本文第 4 节。

## 1. 范围

前三批已经进了 `main`：
- #174：001 / 101 / 102 / 104 / 107；
- #176：103 / 106 / 109 / 110 / 111；
- #177：112 / 113。

本 PR 带进来的是：

| 任务 | 内容 | 合并提交（`feature/katabami`） | 默认 |
|---|---|---|---|
| 105 | “只标记”发布：9 GB 下不抓取，只把过期 raw 标记写进 manifest 并发布新代次，作为恢复路径 | `b3ff694` | 只在 9 GB 档自动走 |
| 108 | 数据库有上限：旧版本被取代满 48 小时删除；JMA 索引只留 48 小时；manifest 只留增量判断需要的记录；一次性 VACUUM 过闸门，之后增量整理 | `ba4f5cf` | **关闭**（`R2_DB_RETENTION`，未设即 off） |
| 601 | 正常发布的每轮记一条容量摘要（`capacity.jsonl`，随代次发布），周报加“容量”一节；9 GB 停止档只做“只标记”发布，不记摘要 | `97f9b3d` | 开启（只读，不新增 R2 写入） |
| 114 | 按本 PR 的 CodeRabbit 意见修 108/601：块删除提交时把待清理的关键词和向量记进 `chunks.sqlite` 的待办表，下一轮续删；块删除成功后才删判龄用的快照；其余 11 条小问题 | `0a04e29` | 随 108 开关 |

## 2. 自动门槛

### 2.1 各任务的调度器验收（各自在全新 checkout 上跑）

| 任务 | 全量测试（排除现存 xfail `test_us_dollar_series_ranks_first_for_gdp_usd`） |
|---|---|
| 105 | 通过 6184，失败 0 |
| 108 | 通过 6214，失败 0 |
| 601 | 通过 6236，失败 0 |
| 114 | 通过 6277，失败 0（基于已合入 `main` 的 `feature/katabami`） |

### 2.2 合进最新 `main` 后（第 3.1 节第 3 条）

`main`（`59fbcab`）合进 `feature/katabami`，无冲突，合并提交为 `7ee1338`。在合并后的代码上运行：

| 命令 | 结果 |
|---|---|
| `make lint` | 0 条 |
| `make check T="tests --deselect=tests/integration/test_wisteria_m1.py::test_us_dollar_series_ranks_first_for_gdp_usd"` | **6262 项通过**，失败、跳过、预期失败均为 0，排除 1 项；用时 1:09:22 |

### 2.3 三档闸门和 9 GB 恢复路径

下面这些文件都包含在上面的全量测试里，单独再跑一次的结果是：连同 `tests/test_cloud_workflow.py`、`tests/test_workflows_cloud_usage.py` 共 207 项通过（46 秒）。

- 闸门：`tests/test_capacity_gate.py`、`tests/test_cloud_capacity.py`。
- 9 GB 恢复：
  - `tests/test_cloud_cleanup_only.py`、`tests/test_cloud_mark_only.py`；
  - `tests/regressions/test_audit_katabami_102.py`、`_105.py`、`_111.py`、`_112.py`、`_113.py`。
- 数据库上限：`tests/test_db_retention.py`、`tests/test_cloud_db_retention.py`、`tests/regressions/test_audit_katabami_108.py`。

## 3. 真实 R2：盘点 → 清理 → 再盘点（2026-11-17，`main` 上已有的清理）

清理用的是 `main` 上已合并的保留规则：旧代次 24 小时、raw 48 小时、孤儿判定。通过 `cloud-usage.yml` 和 `cloud-cleanup.yml`（`delete=true`）手动触发，负责人当场确认“真删”。报告里只有计数和字节。

| 时点 | 运行 | 总量 | 说明 |
|---|---|---:|---|
| 清理前 | 盘点 | 8,362,397,111 B（8.36 GB） | 高于 7 GB 线 |
| 清理 | cleanup `44045501652` | 回收 1,789,284,044 B | 代次 6 个；存储副本 2,896 个，763,368,659 B；raw 3,398 个，1,025,915,385 B |
| 清理后 | 盘点 `44047633398` | **6,558,662,734 B（6.56 GB）** | 存储副本 3,377,213,063 B；raw 3,178,155,434 B；其他小对象 3,294,237 B；可回收 0 |

总量降到 7 GB 以下，满足 901 的要求。

## 4. 第二步（合进 `main` 后，由人做）

**打开开关的前提**：114 已修好删除中途失败后的续删（关键词、向量待办持久化；块删除成功后才删判龄快照）；#194 上 CodeRabbit 剩下的 5 条代码意见转成 KATABAMI-115，**115 合并进 `main` 并通过中断重试的测试之前，不打开 `R2_DB_RETENTION`**。

1. 先用 `cloud-usage.yml`（勾选 `db`）盘点一次，记下总量和数据库副本大小作为基线。
2. 仓库变量设 `R2_DB_RETENTION=on`（本 PR 已在 `cloud-run.yml` 接上这个变量；未设时等于 off）。
3. 手动触发一次 `cloud-run`：
   - 108 在发布前执行，结果在运行报告的 `db_retention` 字段；
   - 第一次的一次性 VACUUM 要过闸门，没放行会写原因，下一轮再试。
4. 用同样的选项再盘点一次，把前后总量和数据库副本大小补进本节。
5. 不通过就在 `main` 上修，期间把变量设回 off。

在第二步通过之前，按第 3.2 节，`main` 上有待验收的版本，暂停一切发布。

## 5. 已知局限

- **`capacity.jsonl` 与旧代码不兼容**：带这个文件的代次，601 之前的代码校验不通过。回滚代码到 601 之前时，需要同时回到不含它的代次，步骤：
  0. 先停用 `Cloud run` 工作流（`gh workflow disable "Cloud run"`），并取消排队中或运行中的 `cloud-run`，**确认这些运行都已结束**（取消被接受后运行可能还在执行）再做第 1 步；从第 1 步到第 3 步完成前不触发任何 `cloud-run`。否则第 2 步之后有运行发布，会生成含 `capacity.jsonl` 的第 M+1 代并让 `current.json` 指过去，回滚失败；
  1. 选定不含 `capacity.jsonl` 的目标代次 M，把 `current.json` 指回 M；
  2. 把编号大于 M 的代次描述符 `generations/<N>.json` 全部移出 `generations/`（例如移到 `generations-rollback/`，确认回滚成功后再删）。不移走的话，旧代码会把它们当成孤立代次，发布一直报 `orphan_generation` 被拒；
  3. 以上两步做完，再部署 601 之前的代码；部署完成后再按需要启用 `Cloud run`。
- **周报容量一节刚上线时数据不全**：头一两周的统计区间里摘要不满 7 天，会写明实际天数，或写“容量数据不足”。
- **停止档不记容量摘要**：连续几轮停在 9 GB 档时，周报会缺少容量最高那段时间的样本。
- **定时抓取仍停着**：`cloud-run` 的定时触发自 11-15 起暂停，等 NAS 回执或新方案。第二步要手动触发。
- **待办续删主要在本机起作用**：云端某一轮失败时整轮不发布，下一轮从上一代已发布的状态重来，所以不会带着待办进入下一轮；114 的续删主要保护本机 `db-retention`。云端失败时的结果是"本轮没删成，下一轮重删"。
