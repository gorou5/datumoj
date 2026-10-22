# DATUMOJ-TACHIBANA-001 完成说明

已完成任务要求的契约修订、假实现、合格测试、SQLite 解析统计和根目录 Schema 导出。两条自动验收均通过，待人工审核；未执行 git commit。

## 实现和逐项核对

| 要求 | 实现与验证 |
| --- | --- |
| 1. 文档类型 | models.py 新增五值 DocKind，Document.kind 默认 None；验证旧 JSON 缺字段仍可读取、所有枚举值和非法类型。FakeParser 填入 bulletin。 |
| 2. 目录快照引用 | Dataset 新增可空 snapshot_sha256 / locator；locator 存在时必须有合法 SHA。验证旧记录、完整引用、仅 SHA、非法 SHA 和 locator。 |
| 3. 变化事件 | 新增 ChangeKind / ChangeEvent，复用 SourceId、DocKey、Sha256、RedactedUrl、AwareDatetime、Locator。验证归属、至少一个资源标识、各变化类型的 SHA 约束、必填且单行的摘要。 |
| 4. 变化日志 | 新增 ChangeLog、FakeChangeLog、ChangeLogSuite。按 ID 幂等，重复保留首次事件；验证过滤组合、空过滤列表、升序、同刻写入顺序、限制和闭区间时间边界。 |
| 5. 失败分类 | models.py 提供 FailureKind / FailureClass 和两个分类纯函数。分类表区分明确 outcome、特定 HTTP 状态及范围回退；逐条测试所有 outcome、所有 5xx、特殊状态和其他 4xx、未知状态。 |
| 6. 父快照引用 | 提供两个 META_PARENT 常量及 parent_ref；不完整、非法 SHA、未去 Key 或非 HTTP(S) URL 返回 None。FetchTask 文档明确传递约定；FakeParser 原样复制有效引用；两个 manifest 后端均验证 meta 到 Document.metadata 的往返。 |
| 7. 解析统计 | Manifest 仅新增 parse_stats；SqliteManifest 仅新增此方法及所需 import，原表结构和其他方法不变。FakeManifest、ManifestSuite 同步。验证来源、抓取时间包含边界、时区、重复内容的多条快照、跨版本和跨解析器、覆盖旧键、无解析记录、无 SHA、孤立解析记录、数据库重开。 |
| 8. 移除本机路径协议 | RawStore 和 FakeRawStore 移除 path_for，通用 Suite 不依赖路径；FileRawStore 未修改。原本机路径布局断言迁至新测试，保留本机覆盖；通用非法 SHA 测试扩展至 open / verify。FakeRawStore 保留 root 构造参数和属性，兼容 IVY 调用方。 |
| 9. Schema | 已运行 export_schemas（通过 python -m datumoj.core.models schemas），新增五份、更新两份根目录 Schema；registry 未修改。测试将临时导出结果与仓库文件比较。 |
| 10. 新旧测试 | 新增 test_contracts_tachibana.py，FakeChangeLog 继承 ChangeLogSuite，FakeManifest / SqliteManifest 继承 ManifestSuite；没有修改 test_models.py、test_contracts.py 或其他已有测试文件。全部已有测试通过。 |

event_id 的固定算法：按 source_id、kind、doc_key、resource_url、old_sha256、new_sha256 的顺序，编码为无额外空白、保留 Unicode 的 JSON 数组，再对 UTF-8 字节做 SHA-256。空字段编码为 null。detected_at、summary、locator 均不参与；测试包含固定编码向量、各身份字段变化和非身份字段变化。直接反序列化也校验 ID 与内容一致，避免绕过 make 写入不稳定 ID。

失败分类的优先级是成功 outcome 返回 None；network_error / 正文格式 / 策略 outcome 优先；其余按 HTTP 状态分类。HTTP 500–599 和 408 可重试，404/410 不可重试，429 可重试，401/403/407 不可重试，未命中的状态归 other 且不可重试。解析失败固定为 format_changed / False。

parse_stats 以每条快照为计数单位，since 按 fetched_at >= since 筛选；同 SHA 只选择最后一次 record_parse 写入的状态，再应用到各条匹配快照。未解析和无 SHA 快照不计入，零计数状态不返回。现有 ParseRecord 没有解析时间字段，因此没有新增时间戳或迁移；SQLite 通过现有 INSERT OR REPLACE 生成的最大 rowid 判断写入顺序，假实现覆盖时重新插入字典键以保持一致。没有采用新增列、改 schema 版本或改其他 manifest 行为的方案。

## 修改文件及目的

- src/datumoj/core/models.py：模型、枚举、稳定事件 ID、失败分类、父引用及 Schema 类型登记。
- src/datumoj/core/contracts.py：变化日志协议、解析统计协议、移除 RawStore.path_for 和父引用传递说明。
- src/datumoj/core/manifest.py：SqliteManifest.parse_stats。
- src/datumoj/testing/fakes.py：同步 RawStore / Manifest、FakeChangeLog、FakeParser 的类型及父引用。
- src/datumoj/testing/contract_suites.py：路径无关的 RawStoreSuite、新解析统计测试及 ChangeLogSuite。
- tests/core/test_contracts_tachibana.py：逐项验证新增约束、协议替换性、持久化和 Schema 一致性。
- schemas/DocKind.schema.json、ChangeKind.schema.json、ChangeEvent.schema.json、FailureKind.schema.json、FailureClass.schema.json：新增导出。
- schemas/Dataset.schema.json、Document.schema.json：更新导出。
- results/DATUMOJ-TACHIBANA-001/：本说明、验收日志、Ruff 基线对比、变更文件清单和范围检查证据。

完整路径清单见 changed-files.txt。

## 验收和证据

| 命令 / 检查 | 结果 | 证据 |
| --- | --- | --- |
| make check T=tests/core/test_contracts_tachibana.py | 342 passed in 0.57s，退出码 0 | contracts-tachibana.log |
| make check T=tests | 1754 passed, 37 warnings in 50.85s，退出码 0 | tests.log |
| git diff --check | 通过 | scope-check.log |
| 修改范围与受保护路径核对 | 全部在 scope 内，未修改 fixtures / registry / 依赖文件 | scope-check.log、changed-files.txt |
| 补充 Ruff 检查 | 相关已有文件基线 11 项、当前 10 项，无新增诊断；当前仍有 10 项已有诊断，未扩展修复 | ruff-comparison.log |

uv 因沙箱禁止写其默认缓存而不可用，两条 make check 均按 Makefile 自带规则使用主仓库只读 .venv，并通过 PYTHONPATH 加载本 worktree 代码。Schema 导出使用同一解释器。未安装、升级或修改依赖及锁文件。

首轮全量测试在兼容修复前失败（11 failed、67 errors），原因是 FakeRawStore 的 root 构造参数被移除。已恢复该参数，随后按原文重跑两条验收命令，以上为最终代码的通过结果。37 条警告来自现有 fork 和 table_names() 弃用提示。

## 兼容性、人工审核及限制

已搜索 src / tests 中 path_for、FakeRawStore 构造和解析记录的调用方。本机路径调用仍由 FileRawStore 提供；其他 path_for 属于 ObservationStore，未受影响。Document / Dataset 新字段可空，旧记录读取保持兼容。Manifest 实现方需要补 parse_stats；Schema 和协议修订需依任务 acceptance.human 由人审查，尤其确认最新记录按写入顺序、统计按快照条数及失败分类优先级。

任务未附“评审中的高置信度质疑”章节；已对照任务十项要求及计划 6.1 节核对。没有引入依赖、外部服务操作、生产数据或新的许可材料。

跳过：真实变化检测、监控、S3/R2、Redaction 和后续解析器实现，均按任务排除；没有外部网络或生产环境验证，也未做大规模性能测试。
剩余风险：SQLite 的“最新”依赖现有 rowid 写入顺序；若未来迁移/重建 parsed 表，需保留顺序或引入显式序号。人工审核尚未进行；Ruff 的 10 项已有诊断仍保留。
