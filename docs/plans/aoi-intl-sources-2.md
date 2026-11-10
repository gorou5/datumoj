# AOI（葵）：国际来源二

> 草案 v1（2026-11-11）。并行小版本之一，规则见 `docs/plans/parallel-versions.md`。任务编号 `DATUMOJ-AOI-<序号>`，分支 `feature/aoi`。

## 1. 一句话

接入 0.4 缺口表里"没有计划、引用最多"的几个国际统计来源，让更多验证案例和融合案例的来源变成可得。

## 2. 依据

`docs/plans/0.4-wisteria-cases.md`（2026-10-24）统计了计划站 130 个案例的来源：没有计划、引用最多的是 Copernicus Data Space（10）、data.bis.org（9）、WHO Global Health Observatory（9）、UNICEF Data（8）、UNFCCC GHG Data（8）、UNIDO Statistics（7）、UN Population Division（7）、FAO FAOSTAT（7）、ESA Earth Observation（7）、中国国家统计局（7）。

本版本取其中**有结构化统计接口、不是遥感栅格**的五个：

| 来源 | 引用案例数 | 预期接口（待 H1 核实） |
| --- | --- | --- |
| BIS（data.bis.org） | 9 | SDMX |
| WHO Global Health Observatory | 9 | OData |
| UNICEF Data | 8 | SDMX |
| FAO FAOSTAT | 7 | API / 批量下载 |
| UN Population Division（World Population Prospects） | 7 | API（可能需要注册） |

Copernicus、ESA 是遥感栅格（0.4 第 5 节不做）；UNFCCC、UNIDO、中国国家统计局放到后续版本，H1 核实后再定。

## 3. 完成标准

1. 每个来源：官方接口、覆盖范围、凭据、配额、robots、许可等级、云端模型条款核实完（H1），写成 `docs/plans/aoi-sources.md`，人确认。
2. 每个来源一个采集器加解析，进观测值库，带单位、口径和发布标识；优先复用已有的 SDMX 解析（Eurostat 的实现，以及 0.4 新增的 IMF、OECD、ILO 的写法），不为几行代码加库。
3. 每个来源至少选 2 个案例需要的序列作为首版范围（在 H1 文档里列出），并各写评测题。
4. 重算 `0.4-wisteria-cases.md` 的可得程度汇总，报告有多少案例从"部分"或"全缺"变为"可得"。

## 4. 不做

- 不接遥感和栅格数据；不全量镜像任何来源，只取案例需要的序列。
- 许可没核实完的不启用，也不对外输出（`no_outflow`）。

## 5. 拥有的目录

- 新文件：`crawlers/` 下每个来源一个文件；`sources/` 下每个来源一个 yaml；`tests/fixtures/` 下各自的样本目录；
- 共用的 SDMX 解析如果需要抽公共函数，先做一个小任务单独合并。

## 6. 任务草稿

| 号 | 线 | 内容 | 依赖 | 审查 |
| --- | --- | --- | --- | --- |
| 301 | C | BIS 采集与观测值转换 | H1 | other_family |
| 302 | C | WHO GHO 采集与观测值转换 | H1 | other_family |
| 303 | C | UNICEF 采集与观测值转换 | H1 | other_family |
| 304 | C | FAOSTAT 采集与观测值转换 | H1 | other_family |
| 305 | C | UN WPP 采集与观测值转换（Key 只从环境变量读） | H1 | other_family |
| 701 | G | 评测题：每个来源 2 题以上（数值题必须走 SQL） | 301–305 | human |
| 702 | G | 重算案例 × 来源可得程度，更新缺口表 | 301–305 | other_family |
| 901 | 集成 | 检查点：五个来源真实抓取一次，评测全部达标 | 全部 | human |

五个采集任务互不依赖，可以由不同的人分别做。

## 7. 人工步骤

| 步骤 | 内容 | 谁 |
| --- | --- | --- |
| H1 | 五个来源的核实（参照 `docs/plans/0.4-wisteria-sources.md` 的格式），选定首版序列 | 认领人 |
| H2 | 需要 key 的来源由人申请，放进环境变量和 Actions secrets | 项目负责人 |
| H3 | 901 验收 | 认领人 |

## 8. 风险

| 风险 | 应对 |
| --- | --- |
| 国际组织条款对再分发或商业用途有限制（0.4 核实中 IMF、UN Comtrade、WTO 都有） | 先 `no_outflow`，逐个核实后再放宽 |
| 限速严（如 OECD 每小时 60 次） | `rate_per_sec` 按官方数字落地，跨请求计数 |
| R2 容量 | 合进 `main` 前给出估算增量；KATABAMI 完成前不做大回填 |
