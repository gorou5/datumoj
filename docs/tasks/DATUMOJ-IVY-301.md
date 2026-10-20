---
id: DATUMOJ-IVY-301
title: 表格读取：CSV / XLSX / ZIP，cp932，表头与注记
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-IVY-003"]
runner: codex
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/tabular/**", "tests/tabular/**", "results/DATUMOJ-IVY-301/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/tabular/
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-301/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否有地方信任了扩展名或 Content-Type", "ZIP 解压是否有上限", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
政府发布的 CSV、XLSX、ZIP 能被可靠地读成带定位的单元格表，编码、合并表头、注记都不出错。

# 背景
开发线：C 统计线。参考旧项目 `runtime/src/adapters/bodik/{text-files,excel}.ts`。踩过的坑：响应头写 UTF-8 实际是 cp932；ZIP 被误判成 XLSX；同一 ZIP 内 SJIS 和 UTF-8 成员混用。样本在 `tests/fixtures/tabular/`。

计划见 `docs/PLAN.md`，任务总表、开发线和跨任务约定见 `docs/TASKS.md`。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。不要 import 其他开发线的实现模块（集成任务除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-IVY-301/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。

# 要求
1. `sniff(bytes)`：按魔数和 ZIP 中央目录判断 csv/xlsx/xls/zip，不信扩展名和 Content-Type。
2. `read_csv`：用 `decode_bytes` 解码，自动判断分隔符，保留原始字符串。
3. `read_xlsx`：openpyxl 只读、`data_only=True`，不计算公式；记录 `sheet`/`cell`；处理合并单元格。
4. `read_zip`：正确解出成员文件名编码，按类型递归；限制成员数量和解压总大小（防 ZIP 炸弹）。
5. `RawTable`：单元格（行、列、原始字符串、locator）、sheet、`member`（ZIP 内路径）。
6. `detect_header`：识别 2–4 行的多行表头，拼接列名；识别表尾注记行（“注：”“資料：”）。
7. 测试覆盖每一条，含 ZIP 炸弹被拒。

# 不做
- 不做统计口径映射。不支持 .xls（只识别并标记 unsupported）。
