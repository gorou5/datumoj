---
id: DATUMOJ-MAPLE-119
title: 现有系统改名 Rikolto：模块移到 datumoj.rikolto，命令、MCP、skill、launchd、NAS 运行包跟着改
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-MAPLE-118"]
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 2, api: [], port: 0}
scope: ["src/**", "tests/**", "skills/**", "schemas/**", "pyproject.toml", "Makefile", "deploy/**", ".dockerignore", ".github/workflows/**", "README.md", "CONTRIBUTING.md", "docs/SETUP.md", "docs/architecture.md", "docs/interoperability.md", "docs/data-sources.md", "docs/deploy/nas.md", "sources/README.md", "results/DATUMOJ-MAPLE-119/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "handoff/**", "plan/**", ".env.example", "sources/*.yaml", "docs/PLAN.md", "docs/TASKS.md", "docs/PLANNING.md", "docs/plans/**", "docs/tasks/**", "docs/acceptance/**", "docs/adr/**", "docs/references/**", "docs/templates/**"]
protected: ["tests/fixtures/**", "uv.lock"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_119.py
    - cmd: make check T=tests/integration
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单（git mv 的移动和内容改动分开列）", "results/DATUMOJ-MAPLE-119/summary.md 完成说明"]
  review: {by: human, checklist: ["模块是否全部用 git mv 移到 datumoj.rikolto、没有留旧路径的转发模块", "按字符串动态加载的模块路径是否全部改到", "持久化的键、环境变量、User-Agent、数据和状态存储布局是否一个都没变", "schemas 重新导出后是否没有差异", "MCP 工具名、参数和返回格式是否不变", "launchd 旧标签是否会被自动卸载、不会两个定时任务同时跑", "除改名外是否没有别的改动", "全部已有测试和各版本端到端测试是否通过", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
现有的采集、解析、索引、存储和问答入口改名 **Rikolto**：代码模块从 `datumoj.*` 移到 `datumoj.rikolto.*`，命令改 `rikolto`，MCP 名称、skill、launchd、NAS 运行包、Actions 配置和现行文档一起改；行为、数据和存储格式完全不变。

# 背景
开发线：A 运行基础。2026-10-24 定了子系统命名（`docs/PLAN.md` 第 13 节第 13 条）：DATUMOJ 是整个项目，现有系统叫 Rikolto，0.4 新建的提取服务叫 Eltiro（以后放 `datumoj.eltiro`）。改名原定在 0.4 做，2026-10-26 移到本版本：改名会动几乎所有文件，要在没有其他代码任务进行时单独做；现在 115–118 已完成，新来源任务还没开工，它们在本任务合并后按新路径写。

计划见 `docs/plans/0.3-maple.md`（第 7 节 119），总计划 `docs/PLAN.md`。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。
- **契约随模块一起移动**：数据模型 `core/models.py`、接口协议 `core/contracts.py`、假实现和契约测试 `testing/` 移到 `src/datumoj/rikolto/` 下，内容只改 import 和模块路径字符串，不改任何定义；确实需要改时写进 `results/DATUMOJ-MAPLE-119/summary.md` 并停下来问。
- 回归测试写在 `tests/regressions/test_audit_119.py`，断言改名以后的正确行为；调度器不接受 `skip` / `xfail`。
- 测试全部离线，临时文件一律放 `tmp_path`，不写仓库里受 git 跟踪的路径。
- 依赖已锁定在 `uv.lock`，不能新增依赖；`pyproject.toml` 只许改 `[project.scripts]` 和工具配置里的包路径，`uv.lock` 不能变（需要变就停下来问）。
- `tests/fixtures/` 下已有的样本只读（README 里的旧命令是录制记录，保持原样）。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、账号 ID、真实主机名或内网地址、本机绝对路径。

# 要求
1. **模块移动**：`src/datumoj/` 下现有的全部子包和模块（admin、analysis、core、crawlers、eval、index、parsers、reference、store、tabular、testing、text 和 changes.py、cli.py、cloud.py、diagnose.py、mcp_server.py、pipeline.py、wiring.py）用 `git mv` 移到 `src/datumoj/rikolto/`，保留历史。`datumoj/__init__.py` 只留项目级说明和 `__version__`；`datumoj/__main__.py` 转到 Rikolto 的命令入口。不留旧路径的兼容层（不做 `datumoj.core` 之类的转发模块）。
2. **字符串形式的模块路径**全部改到新路径：`discover_crawlers` / `discover_parsers` 的默认包、`DATUMOJ_TOOLS_FACTORY` 的默认值、`register_assert_rewrite`、`SKILL_PATH` 等按 `importlib` 或路径加载的地方都要覆盖；`pyproject.toml` 里 ruff、pytest 等配置如有包路径一并改。
3. **命令**：`[project.scripts]` 新增 `rikolto` 指向新入口；`datumoj` 暂留为同一入口的别名，只为本机已装的 launchd 在重装前不断（注释写明 shortcut 和删除时机：本机重装 launchd 并核对 JMA 无缺口后删）。`Makefile`、NAS 镜像的 `ENTRYPOINT` 和用户名、compose 的服务名和命令、`cloud-run.yml` 的命令和 concurrency 组名改用 `rikolto`。`[project] name` 不改。
4. **MCP 和 skill**：MCP 服务名改 `rikolto`，工具名、参数和返回格式不变。`skills/datumoj/` 用 `git mv` 改为 `skills/rikolto/`，SKILL.md 里的名称和命令跟着改，规则内容不变。
5. **launchd**：标签改 `com.datumoj.rikolto.schedule`，plist 调用 `rikolto`。`schedule install` 发现旧标签 `com.datumoj.schedule` 的 plist 还在时，先卸载旧的再装新的；`schedule uninstall` 两个标签都清理。不能出现两个定时任务同时跑。
6. **不变的东西**（回归测试锁住）：
   - 环境变量名 `DATUMOJ_*`；
   - 抓取的 User-Agent `DATUMOJ/0.1 (...)`（robots 规则按它匹配）；
   - 数据目录布局和各库的文件名；
   - 持久化的键和格式字符串，例如 `store/observations.py` 的 `_ORDER_KEY = b"datumoj.snapshot_order"`、状态存储的键前缀、租约 holder 格式、代次清单字段；
   - `schemas/` 的内容：重新导出后没有差异。
7. **现行文档**：`README.md`、`CONTRIBUTING.md`、`docs/SETUP.md`、`docs/architecture.md`、`docs/interoperability.md`、`docs/data-sources.md`、`docs/deploy/nas.md`、`sources/README.md` 里的命令、模块路径和 MCP 注册示例改成新名字；README 用一两句说明 DATUMOJ、Rikolto、Eltiro 的关系。历史文档（计划、任务、验收记录、ADR）不改。
8. **测试**：`tests/` 的目录结构不动，只改 import 和路径字符串。回归测试 `tests/regressions/test_audit_119.py` 断言：
   - 旧路径（如 `datumoj.core`、`datumoj.cli`）导入失败；
   - `rikolto` 和 `datumoj` 两个命令指向同一入口（读 `pyproject.toml` 判断，不依赖已安装的可执行文件）；
   - MCP 服务名是 `rikolto`，工具名清单和改名前一致（把改名前的清单写死在测试里）；
   - launchd plist 的标签和命令，以及旧标签的卸载（用注入的 launchctl，不碰真实系统）；
   - 要求 6 的各项不变；
   - 仓库的代码、测试和现行文档里（排除历史文档、`tests/fixtures/`、`results/`）不再有旧子包形式的导入或 `src/datumoj/<旧子包>` 路径。
9. `make check T=tests` 全部通过，包括 `tests/integration/` 下 IVY、TACHIBANA、MAPLE 的端到端测试。`summary.md` 写清：移动了哪些模块、哪些名字刻意没改及原因、合并后人要做的事（本机重装 launchd 并核对 JMA 无缺口；Claude Code 里按新名字重新注册 MCP；NAS 还没部署，不需要迁移）。

# 不做
- 不改功能、不重构、不顺手修 lint；每个文件只做改名需要的改动。
- 不改项目名、仓库名、环境变量、User-Agent、数据和状态存储的布局。
- 不改历史文档和 `tests/fixtures/`。
- 不建 `datumoj.eltiro`（0.4 的事）。
- 不删 `datumoj` 命令别名。
