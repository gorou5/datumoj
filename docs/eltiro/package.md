---
id: eltiro-package
name: Eltiro offline output package and license gate
version: 1
status: maintained
inputs: [run_id, data_root, Registry, ConsumersConfig, DemandRegistry]
outputs: [consumer_bundle_path, row_count, file_count, gap_count]
runtime: Python with installed pyarrow; local POSIX filesystem
permissions: read host run/registry/demand/gap state; write host audit and selected consumer outbox
sources: generation-pinned observation identities from executor 804
license: authoritative SourceSpec.license.outflow; unknown sources are no_outflow
errors: LicenseGateError with fixed code/source ids; ValueError or OSError for invalid state/storage
validation: make check T=tests/regressions/test_audit_wisteria_805.py
---

# 调用与目录

```python
package_run(run_id, *, data_root, registry, consumers, demands)
```

`registry` 是来源 Registry，`demands` 是 DemandRegistry，`consumers` 接受
ConsumersConfig 或以使用方 id 为键的 ConsumerConfig 映射。返回字典中的
`path` 是 Path，另有 `row_count`、`file_count`、`gap_count`。
不调用网络、模型、执行器或来源存储的写接口。

```sh
eltiro --data-dir data --sources-dir sources package <run_id>
```

成功输出路径与计数的 JSON。许可拒绝时退出 1，只输出 JSON 的固定 `code`
和 `sources`；代码包括 `no_outflow`、`source_forbidden`、
`demand_outflow_forbidden`、`consumer_outflow_forbidden`、`license_changed`、
`invalid_lineage`、`invalid_numeric_type`。其他错误输出 `package_failed`，来源为空。
不输出数值、需求正文、来源正文或底层异常。

```text
<使用方 outbox>/<demand_id>/v<version>/<run_id>/
  data.parquet       # 仅当 output.format 请求
  data.csv           # 仅当 output.format 请求
  manifest.json      # OutputManifest，含数据文件 SHA256 和 citation_index
  provenance.json    # 按数据文件行序排列：符号、实体、时间、来源、发布、数值、引用编号
  gaps.json          # 此需求的运行缺口与同版本同代次的 809 报告
  diff.json          # 与同使用方同需求上次交付的新增/删除/数值变化
  delivery.json      # 使用方、run_id、该需求的交付顺序号与计数
```

每个运行值保留一行，在 `output.fields` 指定的对应符号列中写值；同一行的其他
数值符号列为空，因此保留多来源、多发布和每个值独立的出处。数据文件只含请求的列，
`year` 从运行的 period 取年份，不把未请求的中间运算加入数据文件。
`provenance.json` 保留逐行完整对齐键；即使需求没选 citation_id，也可以按行序查引用。
804 的 `row-0` 转为包内合法 Symbol `row_0`；不改输入运行文件。
Parquet 沿用运行的精确 Decimal 类型，CSV 写十进制字符串，缺值为空，不转浮点、不补零。
没有发布日期时 `released_at` 保持 null，表示 no_data_date，不以抓取时间替代。

# 逐行、逐列许可规则

每个交付行先解析 CitationEntry 的全部输入，包括 source_id、table_key、obs_id、
snapshot_sha256 和 release_id；保留契约中的 operation_chain，舍弃执行器 steps
里的证据片段和自由文字。未知/不完整/重复引用失败关闭。

| 注册表许可 | 该行允许输出 | 失败条件 |
| --- | --- | --- |
| raw_ok | 精确数值、标识；请求了才带注记、序列身份、单位 | 需求或使用方未接受 raw_ok，或来源未获授权 |
| derived_only | 精确数值和标识；引用索引仅身份与运算链 | 需求或使用方未接受 derived_only，或来源未获授权 |
| no_outflow（含未注册） | 无；请求行涉及此来源就阻断整包 | 始终阻断，accept_outflow 无法放宽 |

混合输入按最严格级别决定整行。逐列采用允许列表；derived_only 删除 series、
unit、note 和未知文字列。在混合包中，原文列只在 raw_ok 行取值，受限行为空；
全为受限行时删去这些文字列。未请求符号的行不进入包和索引。
运行记录、画像/缓存、旧清单中的许可副本都不能授权。
同一来源在需求与使用方两个 acceptance 集合中都必须得到接受，不自动降级以绕过收紧。

manifest.license_summary 汇总当前数据和差异引用涉及的来源及最严格许可。
`<data>/eltiro/runs/<run_id>.package-audit.json` 在宿主内部记录通过/拒绝判定；
拒绝记录只有固定代码和来源 id。通过表示许可判定通过，不表示后续磁盘写出一定成功。

# 缺口、差异与重复交付

读取 804 gaps.json 和 809 已生成的
`eltiro/gaps/<demand_id>-v<version>-g<generation>.json`；不在打包期间重新匹配。
核对报告 schema、需求、版本、代次。仅保留本需求的指标键、固定原因码和获授权、
允许流出的来源 id。自由文字、概念正文、证据片段、其他需求字段和汇总报告不复制。
没有 809 报告时只带运行缺口。gap_count 是运行缺口条数加报告中有缺口的指标数。

交付顺序由该使用方、该需求目录中的 delivery.sequence 决定，跨需求版本比较；
不使用其他使用方、其他需求、未交付运行或运行时间先后代替交付顺序。
以符号、国家/地区、period、来源、release_id 对齐，比较 value 和 spread_range，
缺值参与比较，重复对齐键拒绝。第一次 comparable=false、previous_run_id=null。
差异的旧值引用使用 `previous_row_N`，完整身份和运算链也写入清单索引。
前包行也按当前注册表和 acceptance 重新检查，拒绝时整包停止。

重复打包先复核当前许可与授权，再返回已存在包，不覆盖文件、不改变时间。
如果旧包记录的许可与当前注册表不同，拒绝返回旧包，防止 raw_ok 降到 derived_only
后再次交出原文。日期全部来自运行的 started_at；包内不写当前时间。

# 发布与信任边界

输入和输出路径逐级通过目录描述符和 O_NOFOLLOW 打开；拒绝符号链接、目录逃逸、
硬链接输入文件、使用方目录相互重叠及与内部 eltiro 状态重叠。
宿主必须拥有根目录与内部运行/报告/登记状态，inbox 发布者不能修改这些目录。
标准库 flock 串行处理前包选择与发布；不声称跨主机/非 POSIX 锁或对象存储事务能力。

临时目录建在内部 runs 旁，不在任何使用方发件目录。文件与临时目录全部 fsync，
安全创建目标父目录后一次 rename 发布，并 fsync 目标目录。失败清除本次临时目录、
新建空父目录和已改名的本次包，保留已有包。跨文件系统 rename 失败则停止并清理。
不主动推送，不撤回磁盘上已有的历史包。

契约已有 CitationEntry / OutputManifest 数量上限；超限或 Decimal 超出 Arrow 容量时
失败关闭，不截断出处。许可条款和真实来源数据需要另行人工核验，本实现只执行宿主注册表。
