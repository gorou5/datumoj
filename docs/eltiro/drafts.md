---
id: eltiro-page-drafts
name: Eltiro 需求页面草稿
version: 2
status: maintained
runtime: Python 3.12–3.13; standard-library HTMLParser; installed PyYAML/Pydantic
inputs: caller-provided UTF-8 HTML bytes; kind; trusted host configuration; read-only source Registry
outputs: ParsedCases; CaseChanges; draft YAML paths
permissions: local Eltiro data-directory writes; optional model permissions supplied by host
source: project-owned verify/cases pages; page license unknown unless host supplies it
errors: ValueError for invalid input, limits or conflicting drafts; filesystem/SQLite/router errors propagate
tests: make check T=tests/regressions/test_audit_wisteria_810.py
---

# 需求页面转草稿

只提供 Python 接口，不下载页面、不增加 CLI、不登记需求。调用方负责读取 HTML、提供存在的可信数据根目录和带时区的看到时间。模型调用可选；未传 router 时完全不调用模型。

```python
from datetime import UTC, datetime
from datumoj.eltiro.page_cases import parse_cases, record_cases
from datumoj.eltiro.drafts import draft_for_case, list_drafts

# page_bytes / registry / data_dir / consumer_id 由宿主提供。
parsed = parse_cases(page_bytes, "verify")  # 或 "cases"
changes = record_cases(parsed, data_dir=data_dir, seen_at=datetime.now(UTC))
for case in parsed.cases:
    path = draft_for_case(case, consumer=consumer_id, registry=registry, data_dir=data_dir)
paths = list_drafts(data_dir)
```

`ParsedCases` 返回 `kind`、成功的 `cases` 和 `failures`；失败项包含从 1 开始的 article 序号、可识别编号和错误清单。`PageCase` 有 `number`、`title`、`sources`（`SourceLink.text/url`）及按种类区分的字段：

| 页面 | 必填正文选择器 | Python 字段 |
| --- | --- | --- |
| verify | `.hyp`、`.method`、`.signal` | hypothesis、method、signal |
| cases | `.stag`、`.q`、`.fuse`、`.aha` | category、question、fusion、aha |

两页均从 `article.case` 中取 `.no`、`h3`、`.srcs a.src`。编号分别为 `V` 加 2–3 位数字、`#` 加 3 位数字。字段缺失、空字段、重复字段/编号、未闭合 article 单独报告失败，不补猜。移除 `.lab` 界面标签、script/style/template，不采用 data-text 属性。正文仅解 HTML 实体、折叠空白并去首尾空白；不执行指令，不访问链接。

上限：页面 2 MiB、256 案例、64 层、50,000 元素、每字段 4,096 字符。UTF-8 可带 BOM，不信响应头。非 UTF-8、超页面/案例/结构上限、找不到任何 case article 时抛 `ValueError`，调用方不得把错误页当空列表写库；超字段上限是对应案例失败。

`record_cases` 只写 `<data>/eltiro/page_cases.sqlite`。返回 `added/changed/disappeared/unchanged/failures`；分类只比较规范化案例的 SHA256，不使用页面整体哈希、属性、HTML 排版、模型、看到时间或注册表变化。哈希包含编号、种类、标题、该页正文和有序的来源文字/URL。首次看到时间保留，成功案例更新最近看到时间。失败案例的旧记录完全保留；缺编号/编号有歧义时，所有无法对应的旧记录保留，避免误报消失。消失留墓碑，连续缺席不重复报告；同内容重新出现仍算未变，改过内容算改动。不同 kind 独立记录。

草稿默认 `version: 1`（可由调用方指定正整数），ID 为 `verify-v07` 或 `cases-002`。`source_case` 保留页面种类、编号、标题；用途保留 verify 的假设＋方法或 cases 的问题＋融合方法，中间分段。用途中的数值仅保留原文，不转成未经核对的结构化数据。

来源仅精确对应注册表 `base_url` 的主机名和 `allowed_hosts`；不猜来源 ID，不模糊匹配域名，不去掉 www，不随重定向。一个域名对应多个来源时不替人选。不能对应和存在歧义的链接列在 `unconnected_sources`（未接入来源）。偏好来源暂放在一个未命名指标的 `preferred_sources` 中，由人拆成实际指标。

实体、指标概念/单位、时间范围/频率、修订策略、对齐、运算、输出、接受许可、模型预算和运行设置保持空值，并列入 `pending_human`（待人补）。不会照抄样例里的国家代码、表名、指标或运算数值；样例是人编写的完整需求，草稿 ID/用途按上述规则生成。

模型建议需额外传入 `router`（806 实现）、`consumer_config`、`policy`、`budget`、`generation_token`、`run_id`、`providers`、`cache`，以及宿主为页面配置并授权的 `page_source_id`。宿主可传 `page_license: LicenseInfo`；缺省最严 `no_outflow/cloud_model_ok=False`。页面自己的授权身份不从链接推导，也不把链接所指来源的许可当页面许可。

完整案例作为一个不可拆的 `ModelInputPart.content`，标记 `untrusted_source_text`，调用 `plan`，再对可调用项执行 `invoke`，步骤固定 `demand_draft`、请求难度 medium、提示版本 `v2`。812 起内置 `build_prompt` 提供正式模板；没有 `demand_draft/v1`。配置缺此步骤时由 806 排入人工队列。提供方、版本、缓存、预算、降档、重跑（`rerun=True`）及许可重验均由 806 管理。提示词版本改变会改变缓存键，同代次同 run 改版本会因冻结上下文不符而失败，不能用新模板覆盖旧计划；旧计划须原版本重放，新版本用新的运行上下文。路由/提供方失败向上传播，不更换模型。较长案例若超出 806 输入契约会抛校验错误，可不配置模型生成规则草稿。

正式模板只依据案例建议实体、指标、时间范围和频率、运算；数据区仅作数据，禁止估数、编造代码/表名/数值、决定绑定或登记。内置提供方的 `answer.value` 必须是 JSON 对象，键只能是 `entities/indicators/period/operations` 的子集，值必须是非空字符串；未知项不输出，全部未知输出 `{}`。同时保留 810 的分字段提供方接口，字段也只能为这四项的子集。空字段、混合 answer 与建议字段、未知字段、重复 JSON 键或其他结构无效的结果整份不采用，记人工原因，不重试。模型结果字段校验失败也按无建议处理；路由、许可、授权和模型出处错误仍传播。806 未缓存的无效结果在重跑时仍会缺缓存失败。

每个建议保留原始文本，带 `model_generated: true` 和 `model_use`（provider/model/version/tier）；不解析为运算或自动补入需求。建议有了仍需人核对，`pending_human` 保留。缺省页面许可时只能降到许可允许的本地模型或 none，不能进云端。

文件仅写 `<data>/eltiro/drafts/<编号>-<SHA256前16位>.yaml`。临时文件 fsync 后以同目录硬链接原子发布，拒绝目录/文件符号链接，模式 0600；同内容重复生成返回原文件且不改变时间。相同路径下生成文档与原文件不一致（包括不同 consumer、模型建议或人工编辑）时抛错并保留原件，禁止覆盖。`list_drafts` 只列规则命名的普通文件，不读取 YAML、不跟随链接、不创建目录。

顶层 `draft: true` 以及审阅元数据会被 801 的严格未知字段校验拒绝。人需补全空项、审阅建议、删除草稿标记及所有审阅元数据（page_case、unconnected_sources、model_suggestions、model_manual_reasons、pending_human），再按现有流程 `eltiro submit`。仅删标记并不足以使草稿有效。cases 的原编号文件名含 `#`，801 收件文件名规则不接受该字符；编辑完成后另存为如 `cases-002.yaml` 的合法文件名再提交。

状态库和草稿无自动过期；宿主按自己的保留政策清理旧版本。人工修改后的草稿不会被自动覆盖。此模块不写需求注册表、Rikolto、收件箱或发件箱。

812 的模板及 answer JSON 解析使内置适配器可以按上述协议生成建议，依旧由 806 路由调用。本任务只用记录调用的假提供方验证接口；真实模型能力尚未验收。
