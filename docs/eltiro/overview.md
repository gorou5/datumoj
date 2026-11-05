# Eltiro 运营概览（管理页面与每周报告）

`datumoj.eltiro.overview` 只读汇总本机 Eltiro 的运营状态，供 Rikolto 的抓取管理页面（`rikolto admin`）和本机每周报告（`rikolto report weekly`）显示。它不匹配、不运行、不调用模型，也不写任何文件。

```yaml
id: eltiro.overview
name: Eltiro operations overview
version: 1
status: maintained
runtime: Python >=3.12,<3.14
inputs: [data_dir]
outputs: [eltiro.overview/v1]
permissions: [read-eltiro-state]
data_sources: local-eltiro-state (demands.sqlite3, matches.sqlite, alerts.sqlite, runs/*/record.json, reports/gaps-gN.json)
license: unknown
errors: [per-part-fixed-names]
tests: [tests/eltiro/test_overview.py, tests/test_admin_eltiro.py, tests/analysis/test_weekly_report_eltiro.py]
```

## 接口

```python
from datumoj.eltiro.overview import eltiro_overview

overview = eltiro_overview(data_dir)   # dict（eltiro.overview/v1），或 None
```

- `<data_dir>/eltiro` 不存在：返回 `None`，页面和报告写“未启用”。
- 各部分：`demands`（在用需求）、`runs`（最近运行，按完成时间倒序，最多 50 条）、`pending`（待确认匹配）、`alerts`（未确认提醒）、`gaps`（最新一份 `eltiro gaps report` 汇总）。
- `pending` 只算在用需求版本的候选，口径同 `eltiro matches queue`：`confirmation == "pending"` 且不自动绑定。列表最多 50 项，`pending_count`、`alert_count` 是完整计数。
- `gaps` 读取 `eltiro/reports/` 下代次号最大的 `gaps-gN.json`，不重新计算；没有汇总报告时为 `None`。

## 只读与隐私

- SQLite 用 `mode=ro` 打开，状态文件不存在就当作空，不创建目录、库或表。
- 状态文件和目录是符号链接或非普通文件时拒绝读取。
- 输出只有不透明标识（需求 id 和版本、使用方、指标键、来源 id、表键、指标代码）、计数、固定规则名和时间；需求的 concept 和 purpose、匹配理由、证据位置、模型文本一律不带出。缺口汇总里的 concept 原文也会丢掉。
- 每部分独立失败：失败的部分名写进 `errors`，值保持为空。页面和报告会写明“读取失败，显示为空不代表没有”，不显示异常原文。

## 接入点

- 管理页面：`create_app(..., eltiro_overview=callable)` 每次渲染调用一次；不传就不显示 Eltiro 一节。提供函数抛出异常时，只显示“Eltiro 状态读取失败。”，抓取管理部分照常显示。
- 每周报告：`weekly_report(..., eltiro=overview)` 在末尾加“## Eltiro”一节，运行记录只列统计区间内完成的那些。参数缺省为 `False`，不加这一节。
- `rikolto report weekly` 在本机生成时带上这一节；`--from-state` 要和云端发布的周报逐字节一致，而云端没有 Eltiro 状态，所以不带。云端周报（`cloud.publish_previous_week_report`）不变。
