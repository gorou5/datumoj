# #131–133 修复候选及 #134 复现

给 Muse：请审查并按现有协作流程转推本目录的修复补丁。这里不批准合并、生产清理、部署、重启定时或其他生产变更。

## 状态与应用基线

- 固定上游基线：`gorou5/datumoj@2dd1767572da79a48c47f70aee0e4fc7145170a4`。
- 上游 main 的 [CI #411](https://github.com/gorou5/datumoj/actions/runs/42482838032) 成功；这不等于本补丁已有远端 CI。
- 2026-11-08 13:41 UTC 复核：main 仍为该提交，公开 PR/分支未发现 #130–134 对应修复。五个 issue 分配给 aaronjin2023；未排除未公开的本地工作。
- 本分支只存交接材料。用户仓库 main 较旧，请勿把 patch 当作可直接应用到用户仓库 main 的补丁。
- `fixes-131-133.patch` 修改 7 文件，212 行增加、11 行删除；从上述上游基线干净应用后，Git tree 为 `aab1b926b37b681848cf38da53207c28a8f1659f`。
- 补丁 SHA-256：`aea8cf18de1c2e0b38d4f4f5399b41d3e987404015f578fc98a6f143f050ec27`。
- 未改已完成任务副本、schema、依赖、生产配置或公开接口；未向上游写入。

## 三项已验证候选

### #131：保留代次的 raw 依赖

[Issue #131](https://github.com/gorou5/datumoj/issues/131) 在离线真实 StateManager 的双回滚序列中复现：原基线删除一份仍为保留代次恢复所必需的 raw。

修复在删除前汇总所有保留代次的 required raw。沿用调用方已验证当前工作目录的现有契约，相同 manifest SHA 复用引用；不同 manifest 按 SHA 去重，只下载 manifest，并校验分块与整库的大小和哈希。不下载该代次的索引、向量或 raw 正文。缺失、损坏、整库哈希错误或规划超时，均在任何删除之前停止。

新增回归覆盖完整 sqlite/分块两种格式、每个保留代次的 local/remote 恢复、最后依赖代次过期后可正常删除、错误关闭以及去重读取。原有相同 manifest 的 72/144 代次零正文下载回归保持通过。不同 manifest 会增加读取成本，仍受原有清理预算约束；真实 R2 性能未验证。

涉及：
- `src/datumoj/rikolto/core/state.py`
- `tests/core/test_state_retained_raw.py`
- `docs/deploy/runners.md`

### #132：e-Stat 缺少目录 ID

[Issue #132](https://github.com/gorou5/datumoj/issues/132) 原基线因缺少 `@id` 抛 ParseError。候选跳过该条并记录 warning，继续返回后续有效记录，保留原始行号与重复 ID 保留首条规则。

涉及 `src/datumoj/rikolto/parsers/estat_api.py`、`tests/regressions/test_audit_137.py`。

### #133：JMA 空 feed 时间

[Issue #133](https://github.com/gorou5/datumoj/issues/133) 的自闭合、空文本和空白 `updated` 均在原基线抛 ValueError。候选把存在但为空的元素作为未知时间，沿用“无截止点”逻辑。正保留期时仍排入未保存条目，保留 limit 和已有条目跳过规则。完全缺少元素仍报错。存在但为空的元素即使未配置保留期也按未知时间处理。

涉及 `src/datumoj/rikolto/crawlers/jma_feed.py`、`tests/regressions/test_audit_141.py`。

## 验证

所有运行均使用模拟数据、moto 或本地假服务器，没有真实 R2/政府 API 或生产操作。

- 三项均已先在原基线观察到目标断言失败，再验证修复。
- 最终组合针对性测试：132 passed（21.22s）及 122 passed（17.34s），两个文件集合不重叠，合计 254。
- `ruff check src tests` 通过；18份描述 schema 通过；229个 JSON、4个工作流 YAML 解析通过。
- `git diff --check` 和干净基线 `git apply --check` 通过；干净应用的 tree 与受测组合一致。
- 全仓 `pytest -q` 已实际运行并在预设 600 秒限时后中止：3801 passed、1 xfailed、1 warning，601.12s，外层退出码 124，进度约 66%。没有完成全仓，不能宣称全绿。警告来自 test_live_lock_blocks_tick_and_manual_and_crash_releases_stale_lock 的多线程 fork DeprecationWarning。
- Python 3.12.14；已核对测试环境中安装的锁定 registry 包版本，无与此基线 uv.lock 不一致的项目。
- 尚无此补丁的远端 CI，也未合并或部署，不能称线上已修复。

针对性命令（上游基线应用补丁后的仓库根目录）：

```sh
PYTHONPATH=src python -m pytest -q \
  tests/core/test_state_retained_raw.py \
  tests/regressions/test_audit_131.py tests/regressions/test_audit_134.py \
  tests/parsers/test_estat_api.py tests/regressions/test_audit_137.py \
  tests/crawlers/test_jma_feed.py tests/regressions/test_audit_141.py
PYTHONPATH=src python -m pytest -q \
  tests/test_cloud_cleanup_only.py tests/core/test_state.py \
  tests/core/test_state_s3.py tests/regressions/test_audit_135.py
```

## #130 和 #134 没有修复

- [#130](https://github.com/gorou5/datumoj/issues/130) 仍待独立的协调协议审查。本次补丁不解决它；不要把 #131 的绿灯当作所有清理风险解除。此处不扩展未修问题的详细复现材料。
- [#134](https://github.com/gorou5/datumoj/issues/134) 已离线复现：每轮从远端恢复到全新工作目录，连续四次全部嵌入失败后，三个文档的计数每轮仍为 `[1,1,1]`，没有累计到 3。每轮 `generation=null`、current 不变、租约正常释放。
- `issue134_cross_round_repro.py` 是期望行为的**故意失败诊断测试，不是修复**，不在默认 tests 目录、不作为绿色验收。显式运行会在现基线上得到 1 failed；原有 `tests/test_cloud_embed.py` 24 passed（17.82s）仍无法排除此缺陷。
- 现有 `vectors-state.json` 通过不可变代次发布/恢复，没有可直接复用且不发布新代次的跨轮持久化入口。不能通过改写已发布哈希对象或静默发布空代次“解决”。
- 下一步需要审查独立重试检查点设计：绑定代次/文档快照/嵌入模型，持久化只含最少的计数与尝试标识；定义租约接管时的 fencing、并发 CAS/单调合并、幂等、恢复/回滚隔离、清理保留及写入失败行为；保留无成功嵌入不发布新代次的契约。协议尚未实现或批准上线。
- 继续覆盖全失败多轮、部分成功、无积压、禁用来源清理、持久化失败和租约释放；此次 patch 不含重试协议的生产改动。

请回传实际转推 commit/PR 和该提交 CI 结果。若已有他人处理同一项，请先对照，避免重复合入。
