---
id: DATUMOJ-IVY-003
title: 日文文本工具：解码、归一化、和历、数字注记
project: DATUMOJ
status: todo
priority: 1
depends_on: []
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/text/**", "tests/text/**", "results/DATUMOJ-IVY-003/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**"]
acceptance:
  automated:
    - cmd: /opt/homebrew/bin/uv run --frozen pytest -q tests/text/
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-003/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
所有解析器共用一套日文处理：解码、归一化、和历转换、数字和注记解析。

# 背景
开发线：契约。B、C 两条线都要用，不依赖契约，所以和契约同时开工。政府数据里全角数字、和历、cp932 文件非常多；旧项目踩过“响应头写 UTF-8 实际是 cp932”。

计划见 `docs/PLAN.md`，任务总表、开发线和跨任务约定见 `docs/TASKS.md`。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。不要 import 其他开发线的实现模块（集成任务除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-IVY-003/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。

# 要求
1. `decode_bytes(data, declared=None) -> (text, encoding, mismatch)`：识别 BOM、UTF-8、cp932/Shift_JIS、EUC-JP；声明的编码解码失败时退回探测，并标记声明不符。
2. `normalize(text)`：NFKC、统一空白、去零宽字符。
3. `parse_number(s)`：全角数字、千分位、`△`/`▲` 表示负数；`-`、`…`、`x`、`X`、`秘匿`、`－`、`***` 等注记返回 `(None, note)`。
4. `wareki_to_date` / `wareki_year`：令和、平成、昭和、大正，含“元年”、全角数字、漢数字、只到年或年月；年度（`令和5年度`）返回年度标记。
5. `kansuji_to_int`：处理“第二十七条”“百二十三”；`article_number("第二十七条の二") -> "27_2"`。
6. 参数化测试至少 60 个用例，含错误输入。

# 不做
- 不做分词、翻译、CSV。
