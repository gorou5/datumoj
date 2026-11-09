# PR #141：读取代次验证范围与离线复核

给 Muse：已独立复核 [PR #141](https://github.com/gorou5/datumoj/pull/141)，请按既定交接方式转交维护者。此交接不批准合并、工作流触发、凭据设置或其他生产操作。

## 结论

该实现能够补充一个有效代次的读取及工具层冒烟验证，符合 [MAPLE-904](https://github.com/gorou5/datumoj/blob/1e3d6e9baf21ddd4a8db61ace4234e7ae5ae4686/docs/tasks/DATUMOJ-MAPLE-904.md) 要求的有限范围。本次未发现当前调用路径实际写入R2；本地恢复、保留副本与数据存储初始化会写入runner临时目录。“只读”在这里指远端数据操作，不能解读为整个作业没有本地写入、读取成本或占用并发时间。

本次没有访问生产R2、检查密钥权限或触发工作流。凭据最小权限需要维护者另行核实；本报告不是权限审计或安全授权。

## 应修正的描述

PR正文的“**不拉向量**”与实现不符。建议改为：

> 完整拉取已发布代次，包括其向量文件；本次检查只使用关键词检索，不运行向量检索或嵌入服务。记录实际拉取字节数和耗时。

`DATUMOJ_VECTORS=off` 控制查询／嵌入接线，并不让 `pull_state` 跳过已发布向量副本。`pull` 使用 `restore(raw="local")` 恢复全部声明的stores，校验大小、哈希及结构，并下载必需raw与仍存在的可选保留raw。它不保证所有历史raw仍存在，也不是下载bucket内所有对象。

证据：[pull路径](https://github.com/gorou5/datumoj/blob/d4fc39efc39c38b34c9d377799107251716eeebd/src/datumoj/rikolto/core/pull.py#L248-L299)、[恢复实现](https://github.com/gorou5/datumoj/blob/d4fc39efc39c38b34c9d377799107251716eeebd/src/datumoj/rikolto/core/state.py#L924-L1030)、[新增测试明确断言vectors目录存在](https://github.com/gorou5/datumoj/blob/d4fc39efc39c38b34c9d377799107251716eeebd/tests/test_pull_check.py#L141-L152)。904第3条允许必须时整代拉取并记录体积，因此这是描述与运行预算核对，不要求另造部分恢复协议。

## 实际覆盖范围

脚本对一个当前有效代次调用一次 `version`、`coverage`、日文关键词 `search_docs`、第一条命中的 `cite`，以及观测值 `COUNT(*)`。要求代次已校验、检索有受支持来源的命中、引用可还原、SQL结果为非负整数；SQL为0仍可通过。

调用的是 `AnalysisTools` 的函数，不经过真实MCP传输或宿主注册。`version` 由本地pull记录补充后比较代次号；没有再独立取得一套云端问答结果逐项比对。`coverage` 只输出来源快照数，不验证全部来源新鲜度。单条引用还原不等于人工审查全部引用；结构校验和抽样出处验证不等于全历史raw可恢复。

这些范围与904任务一致，不需要为了这个小任务重复建设完整集成套件。已有 [m1集成测试](https://github.com/gorou5/datumoj/blob/d4fc39efc39c38b34c9d377799107251716eeebd/tests/integration/test_maple_m1.py) 使用内存MCP Client/Server，验证两轮发布、云端与副本search/cite/SQL一致、租约互斥、发布失败保护、缺口提醒和周报一致。它使用模拟存储、合成来源和假嵌入，仍是离线证据。

## 本次实际验证

审查head：`d4fc39efc39c38b34c9d377799107251716eeebd`。依赖按该head的 `uv.lock` 冻结安装。

```sh
python -m pytest tests/test_pull_check.py tests/test_workflows_pull_check.py tests/integration/test_maple_m1.py -q
python -m ruff check scripts/pull_check.py tests/test_pull_check.py tests/test_workflows_pull_check.py tests/integration/test_maple_m1.py
```

结果：**16项通过，0失败；相关Ruff检查通过。** 包含新增脚本、工作流静态／shell边界及现有m1集成测试。无运行时代码修改，未独立运行全仓测试。

另已核实该head [CI #414](https://github.com/gorou5/datumoj/actions/runs/42492838556) 成功，日志为5713 passed、1 xfailed、1 warning。不能把PR中的“本地CodeRabbit 0条”当作线上完整审查结论：[线上CodeRabbit仍为额度受限](https://github.com/gorou5/datumoj/pull/141#issuecomment-6723086028)。

## 验收尚需的证据

1. #141当前仍未合并。获得对应批准后，由维护者执行一次真实 `Cloud pull check`，记录run链接、head、代次号、拉取字节／耗时、阶段／退出码及脱敏工具计数。此处没有触发该作业。
2. 将这条真实读取记录与既有手动／定时发布、租约互斥等证据分别链接。m1文档目前正确限定为“模拟存储上的离线端到端通过”；904任务副本的 `status: done` 不能替代人触发及验收记录。
3. MAPLE H4一周试运行、JMA连续性和H5真实多语言问答／人工引用审查分别保留。当前 [计划](https://github.com/gorou5/datumoj/blob/d4fc39efc39c38b34c9d377799107251716eeebd/docs/plans/0.3-maple.md#L193-L216) 写的是11月8日恢复后重计、目标11月15日结束、11月16日问答、11月17日tag。这些是目标，不是已完成事实，也不是本交接新增的执行授权。
4. [PR #135](https://github.com/gorou5/datumoj/pull/135) 已合并到main `ac478731f32269add632325793c80d31fc6ef759`，对应0.2 TACHIBANA记录。其tag或合并不证明0.3一周测试完成。当前main的 [CI #417](https://github.com/gorou5/datumoj/actions/runs/42533369156) 成功同样只证明CI范围。

903/904任务副本已有独立 [PR #140](https://github.com/gorou5/datumoj/pull/140)，不重复开集成任务。本交接只提交审查与验证结果；没有合并、部署、修改来源／清理设置或运行生产。
