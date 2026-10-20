# DATUMOJ-IVY-102 完成说明

## 做了什么
- `src/datumoj/core/manifest.py`：`SqliteManifest`，实现 `Manifest` 协议的全部方法（抓取记录、meta、按 URL/来源/sha256 查询、失败汇总、新鲜度、`parsed` 解析状态表）。
- 写入都走 `BEGIN IMMEDIATE` 事务；`snapshot_id` 唯一，重复抛 `DuplicateError`；meta 非 JSON 抛 `ValueError`。
- `schema_version` 表：版本不符或库里没有版本记录时抛 `SchemaVersionError`，消息写明代码要求的和库里的版本。
- `tests/core/test_manifest.py`：继承 `ManifestSuite` 全部通过，另补持久化、各类失败、重复写入不留残余、两连接并发写（两线程各自连接）、跨连接重复、版本不符、无版本表、`NO_PARSER`、带时区偏移的排序。

验收：`make check T=tests/core/test_manifest.py` → 25 passed。

## 假设 / 没做
- 时间以 UTC 定宽字符串存储排序，快照本身存 JSON，所以还原时保留原时区偏移。
- `last_success` 只认 `outcome=ok`（按协议和合格测试）；`freshness` / `failure_summary` 里“成功”指 ok 和 not_modified。
- 没有迁移逻辑（版本不符只报错）；启用 WAL 模式，会在库旁产生 `-wal`/`-shm` 文件。
- 未跑全量历史测试（只跑了验收命令）。
