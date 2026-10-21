---
id: DATUMOJ-IVY-306
title: 修复观测值库只读查询的日文字面量错误（字节偏移误当字符下标）
project: DATUMOJ
status: todo
priority: 1
depends_on: ["DATUMOJ-IVY-303"]
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/store/observations.py", "tests/store/test_observations_unicode.py", "results/DATUMOJ-IVY-306/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/store/test_observations.py"]
acceptance:
  automated:
    - cmd: make check T=tests/store/test_observations_unicode.py
    - cmd: make check T=tests/store/test_observations.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-306/summary.md 完成说明"]
  review: {by: other_family, checklist: ["日文和其他多字节字面量是否都能正常查询", "禁用函数名检查在多字节 SQL 里是否仍然准确（不误判、不漏判）", "原有 303 的测试是否全部仍然通过", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
`DuckDBObservationStore.query_readonly` 在 SQL 里含日文等多字节字面量时能正常执行，禁用函数名的检查在这种 SQL 里也照样准确。

# 背景
开发线：C 统计线（修复 DATUMOJ-IVY-303 已合并的代码）。评测题审查时发现并已复现：

```python
DuckDBObservationStore(tmp).query_readonly("SELECT '東京都' AS a")   # IndexError: string index out of range
```

原因：`src/datumoj/store/observations.py` 的白名单检查 `_guard` 用 `duckdb.tokenize(sql)` 返回的位置去切 Python 字符串，但这个位置是 **UTF-8 字节偏移**，不是字符下标。SQL 里一出现多字节字符，后面的位置就全部错开：轻则抛 `IndexError`，重则 `_word` 读错单词，禁用函数可能被放过或误拦。DATUMOJ-IVY-703 的经济普查 SQL（含 `'東京都'`、`'情報通信業'` 等）会触发这个错误。

计划见 `docs/PLAN.md`，任务总表见 `docs/TASKS.md`。测试全部离线；依赖已锁定，不能改 `pyproject.toml`。

# 要求
1. 修正 `_guard`（及其他用到 tokenize 位置的地方）：先把 SQL 编码成 UTF-8 字节，按字节偏移切片后再解码；或者改用其他不依赖偏移的方式。不要放松任何原有的拦截规则。
2. 新测试写在 `tests/store/test_observations_unicode.py`（新文件），至少覆盖：
   - 含日文字面量的普通 SELECT 能执行并返回正确结果（例如 `WHERE area_label = '東京都'`）；
   - 日文字面量放在禁用函数**之前**时，禁用函数（如 `read_csv('/etc/passwd')`）仍被拒绝；
   - 字面量里出现禁用函数名的文字（如 `'read_csv'`、`'東京read_csv'`）时**不被误拒**；
   - 全角字符、emoji、混合 ASCII 与日文的列别名和注释。
3. 原有的 `tests/store/test_observations.py`（303 的 83 个测试）必须全部仍然通过，不能修改它（已列为 protected）。

# 不做
- 不改观测值库的存储格式和其他行为。
- 不改契约。
