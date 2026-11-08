---
id: DATUMOJ-MAPLE-137
title: 解析容错：e-Stat、BODIK、data.europa、法令的单条坏数据不再拖垮整页
project: DATUMOJ
status: done
priority: 1
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 2, api: [], port: 0}
scope: ["src/datumoj/rikolto/parsers/estat_api.py", "src/datumoj/rikolto/parsers/ckan_meta.py", "src/datumoj/rikolto/parsers/data_europa.py", "src/datumoj/rikolto/parsers/xml_law.py", "tests/parsers/test_estat_api.py", "tests/parsers/test_estat_dataset.py", "tests/parsers/test_ckan_meta_snapshot.py", "tests/parsers/test_data_europa.py", "tests/parsers/test_xml_law.py", "tests/regressions/test_audit_137.py", "results/DATUMOJ-MAPLE-137/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "docs/tasks/**", ".env*", "sources/**", "catalog/**", ".github/**", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/index/**", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/parsers
    - cmd: make check T=tests/regressions/test_audit_137.py
    - cmd: make lint
    - cmd: make check T="tests --deselect=tests/integration/test_wisteria_m1.py::test_us_dollar_series_ranks_first_for_gdp_usd"
  evidence: ["测试输出日志", "变更文件清单", "未升解析器版本的说明"]
  review: {by: other_family, checklist: ["单位比较放宽后，真正不同的单位是否仍然报错", "跳过的条目是否都留下 warning，没有静默丢数据", "HYPERLINK 和 file:// 这类值是否绝不会进入 url 字段", "解析器 version 是否没变", "放宽法令大小上限后是否仍有上限"]}
  human: sampled
max_attempts: 5
---
# 目标
个别脏数据只影响它自己，同一页或同一响应里的其他数据正常入库。

# 背景
2026-11-06 用 R2 上真实失败的 raw 复现的原因：
- **e-Stat（`estat_api.py`）**，3 个问题，这个来源目前没有数据入库：
  - `getStatsData` 的 `@unit` 是全角 `％`，配置是半角 `%`，报 `unit mismatch`。
  - `getStatsList` 里有重复的 `@id`（如 `0004021108`），报 `dataset_key 重复`。
  - `getStatsList` 里 description 来源字段有时是对象（含 `TABULATION_SUB_CATEGORY...`），不是字符串，报校验错误。
- **BODIK（`ckan_meta.py`）**：有 package 的 `url` 是 `=HYPERLINK("#", "https://...")`，报 `CKAN package 字段无效`，整页失败。
- **data.europa（`data_europa.py`）**：有 distribution 缺 ID 或 URL，报 `distribution missing ID or URL`；有数据集 URL 是 `file:///usr/verticles/...`，报校验错误。
- **法令（`xml_law.py`）**：`MAX_XML_BYTES` 是 16 MB，两部法律（约 19 MB、22 MB）超限。

任务分支从 `feature/0.4` 拉出，合并回它。测试全部离线，不新增依赖；不写账号、密钥、本机绝对路径。

# 要求
1. e-Stat：
   - 单位比较前做 NFKC 规范化，`％` 和 `%` 视为相同；真正不同的单位仍然报错。写入的单位值保持配置里的值。
   - `getStatsList` 按 `@id` 去重，保留第一条，记 warning。
   - description 是对象时，把其中的文字值按顺序拼成字符串；其他非字符串类型按没有描述处理。
2. BODIK / CKAN：package 的 `url` 不是合法的 http(s) URL 时，按没有 url 处理并记 warning，其余字段照常入库。其他字段无效的行为不变。`url` 无效时必须和“没有 url”结果一样：记 warning 后走原有的 `landingPage` 后备，`landingPage` 也无效才设为 None；两种情况都要有回归测试（2026-11-06 审查打回）。
3. data.europa：
   - distribution 缺 ID 或 URL，或 URL 不是 http(s)（比如 `file:///usr/verticles/...`）时，只跳过这一个 distribution，记 warning。真实失败的 `nuts-2010-03m` 可能属于这种情况。
   - 数据集 URL 不是 http(s) 时按没有 url 处理；如果 Dataset 模型要求必填，就用现有的后备值，不能编造。
4. 法令：`MAX_XML_BYTES` 提到 32 MB，超限仍然报错。确认 60 秒解析预算下，约 22 MB 的合成输入能解析完。
5. **不升任何解析器的 `version`**。之前失败的文件每轮本来就会重试（`pipeline.py` 的 `is_retry`），升版本会让所有文件重新解析。这些改动只影响以前失败的输入。结果说明里写明这一点；如果某个已有的成功输入解析结果变了，那就是回归，要修。
6. 新增 `tests/regressions/test_audit_137.py`，覆盖上面每种情况，并断言同一响应里其他数据正常产出。fixtures 不改，测试数据写在测试里。
7. `make lint` 0 条，全量测试全部通过，要跑完，不要中途中断。全量命令排除 `test_wisteria_m1.py` 里现存的 1 个 xfail（调度器把 xfail 算作失败，与本任务无关）。不要修改那个测试，也不要新增 xfail 或 skip。
8. 调度器里没有人能回答问题，不要等待回复。遇到范围缺口，在 scope 内选最小的做法完成，写进结果说明。

# 不做
- 不查 `325M50080000018`（法令 XML 不能转换为文档块），这一部暂时放着。
- 不改 `pipeline.py`、`cloud.py` 和来源判坏规则（任务 138 负责）。
- 不改 `sources/*.yaml`，不访问真实网络和 R2。
