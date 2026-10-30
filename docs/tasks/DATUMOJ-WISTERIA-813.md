---
id: DATUMOJ-WISTERIA-813
title: Eltiro 输出包口径修正：derived_only 行带单位；差异对齐键加序列身份摘要；记下模型档全为 none 时不自动绑定的口径
project: DATUMOJ
status: done
priority: 2
depends_on: ["DATUMOJ-WISTERIA-805", "DATUMOJ-WISTERIA-807"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/eltiro/license_gate.py", "src/datumoj/eltiro/package.py", "docs/eltiro/package.md", "docs/eltiro/model_match.md", "tests/eltiro/test_license_gate.py", "tests/eltiro/test_package.py", "tests/regressions/test_audit_wisteria_813.py", "results/DATUMOJ-WISTERIA-813/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "docs/PLAN.md", "docs/plans/**", "docs/adr/**", "sources/**", "schemas/**", "eval/**", "src/datumoj/rikolto/**", "src/datumoj/eltiro/models.py", "src/datumoj/eltiro/contracts.py", "src/datumoj/eltiro/cli.py", "src/datumoj/eltiro/executor.py", "src/datumoj/eltiro/runs.py", "src/datumoj/eltiro/gaps.py", "src/datumoj/eltiro/matching.py", "src/datumoj/eltiro/model_match.py", "src/datumoj/eltiro/rematch.py", "src/datumoj/eltiro/routing.py"]
protected: ["tests/fixtures/**", "src/datumoj/eltiro/testing/**", "docs/plans/0.4-wisteria-samples/**", "tests/regressions/test_audit_wisteria_804.py", "tests/regressions/test_audit_wisteria_805.py", "tests/regressions/test_audit_wisteria_807.py", "tests/regressions/test_audit_wisteria_811.py"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_wisteria_813.py
    - cmd: make check T=tests/regressions/test_audit_wisteria_805.py
    - cmd: make check T=tests/regressions/test_audit_wisteria_811.py
    - cmd: make check T=tests/eltiro
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-WISTERIA-813/summary.md 完成说明（含三种许可等级各自输出的列、差异对齐键的组成、旧交付记录怎样比较）"]
  review: {by: other_family, checklist: ["derived_only 行是否只多了 unit 一列，series、note 和其他来源文字仍不输出", "no_outflow 行是否仍不出现、整包规则是否不变", "差异对齐键里的序列身份是否只用摘要，diff.json 和包内任何文件不出现序列名称等原文", "同一来源两个序列的包是否不再报错、差异逐序列对齐", "旧交付没有序列摘要时是否按规则处理、不误报全部新增删除", "805、811 回归测试是否原样通过", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
2026-10-28 人定了 805、807 审查留下的三处口径，本任务按定下的口径改：
1. `derived_only` 的行带上单位；
2. 差异对齐键加上序列身份，同一来源有两个序列时不再报错；
3. 模型档打开但路由全是 `none` 时，原本会自动绑定的精确匹配也进人工队列，维持现状，只写进文档。

# 背景
开发线：Eltiro。设计见 `docs/plans/0.4-wisteria.md` 第 6.5、6.6 节和 ADR 0006 的许可闸门一节。已完成的实现：
- 805 的 `license_gate.py`：列按白名单分三组。`IDENTIFIERS`、`NUMBERS` 所有许可等级都出；`RAW_TEXT = ("series", "unit", "note")` 只在 `raw_ok` 行出。
- 805 的 `package.py`：`_difference` 按 `_KEY = (symbol, country, region_scheme, region, period, source, release_id)` 对齐上一次交付。键里没有序列，同一来源两个序列在同一实体、时间上各有一行时抛 `ambiguous difference identity`，整包失败。
- 804 的运行结果里 `series` 列是 `series_key(series)` 的 JSON，含指标名称和维度，属于来源文字；`unit` 是序列的单位文字。
- 807 的 `docs/eltiro/model_match.md` 已写明：路由为 `none` 或预算用完时清除自动绑定、进人工队列。

本版本在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- 本任务只改 `license_gate.py`、`package.py` 和对应测试、文档；执行器、运行目录格式、命令行、匹配都不改。
- Eltiro 的模型、契约和测试工具只读；Rikolto 的代码一律不改。需要改契约或模型时，写进 `summary.md` 并停下来问。
- 804、805、807、811 的回归测试不许改，必须原样通过。805 回归测试引用了 `tests/eltiro/test_package.py` 里的 `test_derived_only_never_copies_source_text_anywhere_in_bundle`：本任务可以改这个测试里 `unit` 的断言（单位现在允许出现），但 `series`、`note` 的标记字符串仍必须断言不出现，测试名不变。
- 回归测试写在 `tests/regressions/test_audit_wisteria_813.py`；调度器不接受 `skip` / `xfail`。
- 测试全部离线，临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、账号 ID、真实主机名或内网地址、本机绝对路径。

# 要求
1. **derived_only 带单位**：
   - 把 `unit` 从 `RAW_TEXT` 移到所有可输出等级都带的一组（名字自定，例如 `UNITS`）；`derived_only` 和 `raw_ok` 行都输出单位，`no_outflow` 行仍不出现。
   - `series`、`note` 和白名单以外的列仍只在 `raw_ok` 行输出；混合包里受限行这些列为空。
   - 单位的长度沿用契约的 `MAX_LABEL_LENGTH`，超长或含换行等控制字符时这一行的单位置空，并记进闸门审计记录，不整包失败。
   - `docs/eltiro/package.md` 写明：单位是数值的必要说明，2026-10-28 人定可随 `derived_only` 流出。
2. **差异对齐键加序列身份**：
   - 对齐键加一项序列摘要：`series` 列的 SHA256 前 16 个十六进制字符。摘要是标识，可以写进 `diff.json`；序列原文仍不写。
   - 同一来源两个序列的包能正常打出，差异逐序列对齐。
   - 上一次交付的差异记录里没有序列摘要时（805 格式），在旧记录里该键唯一的情况下按不带摘要的旧键对齐；不唯一时这次的差异标明"上次交付无法逐序列比较"，不报错、不把全部行列成新增和删除。
   - 同一运行重复打包仍字节相同。
3. **模型档口径**：在 `docs/eltiro/model_match.md` 写一句：2026-10-28 人定，打开模型档而路由全为 `none` 时，精确匹配也不自动绑定、进人工队列；不改代码。
4. **回归测试** `test_audit_wisteria_813.py` 覆盖：
   - `raw_ok` 行带单位、注记和序列；`derived_only` 行带单位，不带注记和序列（用带标记字符串的合成数据断言整包所有文件）；`no_outflow` 行不出现；
   - 超长或带控制字符的单位被置空并记审计；
   - 同一来源两个序列、同一实体同一时间的包能打出，差异逐序列正确；
   - `diff.json` 里没有序列原文，只有摘要；
   - 上次交付是 805 格式时，唯一能对齐的按旧键比较，不能对齐的标明无法比较、不报错；
   - 同一运行重复打包字节相同；
   - 804、805、807、811 的回归测试原样通过。
5. `make check T=tests` 全部通过。

# 不做
- 不放开 `series`、`note` 或其他来源文字；不改 `no_outflow` 整包规则。
- 不改执行器、运行目录格式、命令行和匹配逻辑；不改模型档行为。
- 不改 Rikolto 的代码，不改 Eltiro 的模型和契约。
