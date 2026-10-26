# datumoj

> **English**: datumoj connects official public data sources from governments around the world. We connect data — we don't hoard it. Contributors build reusable connectors, skills, agents and tools around official APIs and public interfaces, following shared standards for discovery, invocation and evidence.

**datumoj（数据之门）** 是一个开源项目：连接世界各国及地区政府侧公开的数据，为用户定义的场景提供数据访问、检索、处理、分析和自动化能力。

DATUMOJ 是整个项目；现有的采集、解析、索引、存储和问答系统名为 **Rikolto**（`datumoj.rikolto`，命令 `rikolto`）。计划中的提取服务 **Eltiro** 将在 0.4 版本独立实现。

## 核心原则

1. **连接数据，而不是占有数据** — 我们不是传统的数据搬运工。
2. **优先连接官方数据源** — 官方 API、官方数据接口、公开下载接口及其他合法访问方式，而不是复制和囤积原始数据。
3. **可复用能力是核心资产** — 数据发现、连接、转换、路由、调用和处理能力，以及 Skills、Agents、MCP Servers、工作流；原始数据副本不是默认资产。
4. **用户定义场景，项目提供连接能力** — 不预设唯一的业务应用。
5. **三个统一标准**（详见 [docs/interoperability.md](docs/interoperability.md)）：
   - 统一**描述**标准 — 让模块能被发现；
   - 统一**调用**标准 — 让模块能组合；
   - 统一**证据**标准 — 让数据可追溯。
6. **长期可维护、低协作成本、安全、可组合**优先。

## 能交付什么

Skills / AI Agents / Prompts / MCP Servers / 政府数据源连接器 / API 适配器 /
数据解析转换工具 / Cloudflare Workers / 定时任务与事件驱动工作流 /
检索分析工具 / 本地运行程序 / 可视化工具 / 数据源目录 / 示例与集成方案……

不强制语言、不强制 AI coding agent、不强制运行平台。只要符合统一接口、安全规范和许可要求，用最适合场景的实现方式。

## 快速开始

```bash
git clone https://github.com/gorou5/datumoj.git
cd datumoj
# 阅读：
#   AGENTS.md        —— AI coding agent 工作约定（必读）
#   CONTRIBUTING.md  —— 人类协作者流程
#   docs/architecture.md —— 架构与三个统一标准
#   docs/SETUP.md     —— 完整开发环境（Python 包与测试套件）
```

注册层标准最小验证（需要 Python 3.9+）：

```bash
pip install jsonschema pyyaml
python tests/validate_schemas.py
```

## 目录结构

```
├── README.md / AGENTS.md / CONTRIBUTING.md / SECURITY.md / CODE_OF_CONDUCT.md
├── CLAUDE.md           # 调度器（orchestrator）协作约定，内部工作流
├── src/datumoj/rikolto/ # Rikolto Python 包：core / crawlers / parsers / index / store / eval / admin / mcp_server
├── sources/            # 数据源描述（YAML）：eGov 法令・e-Stat・JMA・政府统计门户等
├── schemas/            # 内部数据契约（*.schema.json）＋注册层描述标准（registry/）
├── docs/               # PLAN · SETUP · PLANNING · TASKS · architecture · interoperability · data-sources · adr · tasks
├── examples/           # 真实、可运行的最小示例
├── tests/              # pytest 测试套件 ＋ Schema 校验脚本
├── eval/               # 评测问题集
└── .github/            # CI · Issue/PR 模板
```

## 贡献

- 人类协作者：读 [CONTRIBUTING.md](CONTRIBUTING.md)，从分支 + Pull Request 开始。
- AI coding agent：先读 [AGENTS.md](AGENTS.md)，遵守修改范围、安全边界与报告格式。
- 新数据源提案：用 `.github/ISSUE_TEMPLATE/new-data-source.md` 模板。
- 内部任务调度（orchestrator）工作流见 [CLAUDE.md](CLAUDE.md) 与 `docs/SETUP.md`、`docs/PLANNING.md`；任务文件在 `.orch/tasks/`，不进 git。

## 当前阶段

0.1（代号 IVY）开发中：已实现日本官方数据源的抓取、解析、索引与检索（eGov 法令、e-Stat 文件、JMA 订阅、政府统计门户），含 MCP Server、本机抓取管理页面与评测集。数据源注册层的三个统一标准（描述 / 调用 / 证据）已并入仓库，正在与内部数据契约对齐。许可证待定（见下）。

## 许可证

暂未确定。候选：**MIT**（最宽松，利于复用与商业集成）或 **Apache-2.0**（含专利授权条款）。待维护者确认后添加 `LICENSE` 文件。在此之前，所有贡献默认视为同意未来采用项目选定的开源许可证。

## 安全

发现安全问题请勿公开提 Issue，见 [SECURITY.md](SECURITY.md)。
