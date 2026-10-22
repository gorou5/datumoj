# DATUMOJ-TACHIBANA-902 完成说明

本任务将 m2 的真实实现接在一起离线验收；唯一运行时代码变更是 CKAN 分页接线适配器。未修改开发线实现、公共契约、fixtures、依赖、sources 或已提交 catalog。

## 实现与范围

- `src/datumoj/wiring.py`：`_CkanPages` 跳过 `role=datastore` 的目录解析，保留 runner 已写入的 raw/manifest 快照。此前这些页被误交给 package 解析器，因缺少 package id/name 而失败。保留现有版本 2、resource/activity 分派及 JSON 校验；流水线会自动重试失败记录，集成测试已显式构造旧失败记录并验证恢复。另整理同一文件的 import 顺序。
- `tests/integration/test_tachibana_m2.py`：新增真实 crawler → raw/manifest → parser → index/store → tools 集成测试，继承 ParserSuite；复用 IVY 集成测试的全量语料初始化及四个题库选择逻辑，不改现有测试和阈值。
- `results/DATUMOJ-TACHIBANA-902/**`：验收日志、显式导出的证据、变更清单及本文。

优先复用已有接线、存储适配器、采集器、解析器和注册导出；无需新增 crawler、存储包装或修改 CLI/sources。DataStore 在本任务中作为原始快照保存，不将设施记录误写为目录数据集。

## 按要求核对

1. **BODIK**：两页原始 package_search 的 6 个目录记录全部入库并可 cite；DataStore 使用 `datastore_limit=5`，真实录制的 offset 0/5/10/65 字节原样保存，完整请求 offset 0..65 共 14 页且每页有成功快照和解析记录，许可元数据保留为 `cc-by-40-intl`。offset 65 仅 2 条，之后不请求 67，也不跟随 `_links.next`。67 空页已登记回放路由，预期不被请求。
   - 录制样本没有 offset 15..60，测试仅在临时目录构造 10 页带 `synthetic: true` 的分页占位记录，既不伪造设施名称/地址，也不宣称掌握全部 67 条真实设施记录。真实设施证据限于录制的 17 条。
2. **e-Stat**：三张原表均能按配置中的完整表题找到，目录出处可 cite 还原；SQL 分别核对人口 XLSX 第一张表 E24、东京及大阪 cp932 CSV 的 I977，全部与原文件相同。CSV 保留错误 UTF-8 响应头以验证内容识别。另核对 L15 的 `-` 保存为空值及原注记，没有补零。
3. **e-Gov 目录**：回放两个原始分页，10 条结果都有 citation；每条 cite 还原出的 JSON 与对应原始 results 条目完全相等。
4. **关键词门槛**：`DATUMOJ_VECTORS=off`，真实关键词索引评测 IVY 四题库共 60 题；沿用 `test_full_eval.py` 使用的 `check_gate` 和阈值，全部通过。behavior 中两道预期零命中题显式断言为 `zero_hits`。
5. **存储适配层**：通过 `DATUMOJ_RAW_BACKEND=s3` 和端点环境变量，向随机端口的 `127.0.0.1` moto server 接入真实 S3RawStore。测试对 botocore 每次 HTTP 发送断言目标必须为该端点。法令样本 crawl → parse → index → search → cite 与 FileRawStore 的请求、完整检索结果及还原 passages 完全相同；逐个核对 S3 对象与 raw 字节，S3 模式没有本机 raw 目录。
6. **注册层**：真实 CLI `registry export --out <tmp_path>` 的全部 7 个 JSON 与已提交 catalog 字节一致，并执行原有 `tests/validate_schemas.py`。
7. **仓库写入边界**：数据目录、合成发现及分页文件均在 pytest 临时目录；证据默认临时保存，只有显式设置 `DATUMOJ_EVIDENCE_DIR` 才导出。本文所附证据来自设置该变量为本任务 results 下 evidence 的专项验收；全量验收使用默认临时路径。另比较全量测试前后的 tracked diff，核对现有受跟踪文件未被测试改写。
8. **已有测试**：全量验收结果见下。

## 验收

- `make check T=tests/integration/test_tachibana_m2.py`：13 passed；见 `acceptance-m2.log`。
- `make check T=tests`：2054 passed，37 条既有弃用警告，耗时 83.74 秒；见 `acceptance-all.log`。
- `tests/validate_schemas.py`：8 个描述文件全部通过；见 `schema-validation.log`。
- 变更文件 Ruff 检查及 `git diff --check`：通过。
- make 提示 uv 不可用（可能是缓存权限），并按项目既定规则只读使用主仓库 `.venv`；未安装或升级依赖，未改锁文件。
- `acceptance-all-first.log` 保留首轮回归诊断：解析器版本提升触发现有版本断言。最终保留版本 2，并用旧失败记录重试测试验证兼容性；未修改该既有测试。

关键词评测的现行门槛与实测值：

| 指标 | 门槛 | 实测 |
| --- | ---: | ---: |
| recall_at_10 | 0.85 | 0.9807692308 |
| numeric_precision | 1.0 | 1.0 |
| numeric_occurrence | 1.0 | 1.0 |
| gap_accuracy | 1.0 | 1.0 |
| citation_recoverability | 0.95 | 1.0 |

完整报告保留了两道单题未完全达标的记录：`behavior-time-estat-period-publication` 缺统计期间或发布日，`stat-estat-tokyo-ict-employees-2016` 的预期检索结果未全部出现在前十。时间语义准确率为 0.8333333333；它不是现行 `check_gate` 的硬门槛。数值及引用恢复校验通过。这些记录未被隐藏或通过改题、降阈值处理。

## 人工验收与未验证事项

待任务要求的人工验收：确认范围、BODIK 合成分页的证据边界、三表数值，以及 moto 端点限制。未 git commit、合并或打 tag；`v0.2.0-tachibana-m2` 应在合并并通过人工验收后由发布流程处理。

未访问真实政府接口、R2 或其他生产服务；未做线上吞吐及真实云权限验证。沿用现有许可声明，没有扩大再分发权限。保留解析器版本，既有成功记录继续增量跳过，既有失败记录在下次解析时自动重试；未迁移用户数据。

跳过：线上与生产验证、发布操作。残余风险：录制中段缺页、上述两道单题缺口；没有修改 scope 以外的实现来补救。
