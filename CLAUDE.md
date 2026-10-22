# CLAUDE.md：DATUMOJ

## 项目
- 项目名（调度器里）：`DATUMOJ`；任务 ID：`DATUMOJ-<版本代号>-<序号>`，当前版本 0.1 代号 `IVY`（蔦），即 `DATUMOJ-IVY-<序号>`。版本代号取家纹，规则见 `docs/PLAN.md` 第 8 节
- 目标与计划：见 `docs/PLAN.md`
- （在这里补充项目自己的规则：语言、目录结构、测试命令、编码约定……）

## 调度器（orchestrator）
**先判断你是谁：**
- 你在和人直接对话：本节适用。
- 你是被调度器拉起来执行某个任务的：你收到的是调度器的任务包，当前目录在调度器的 worktrees 目录下。这时**忽略本节**，只按任务包做，不要运行任何 `orch` 命令。

### 路径
- 命令：一律在仓库根目录用 `bin/orch …`。它是个包装脚本，里面写好了调度器的 Python 和配置文件路径；换配置只改它。
- 操作规则：严格按 `<调度器仓库>/docs/AGENT-GUIDE.md`（调度器仓库的位置见本机 `bin/orch`；没有就从 `bin/orch.example` 复制）。
- 任务文件：`.orch/tasks/`。格式见 `<调度器仓库>/docs/formats.md` 第 1 节，模版是 `docs/templates/task.md`。

### 每次对话开始
1. 先跑 `bin/orch attention --project DATUMOJ`，用人话讲给人听：卡在哪、是什么、可选的处理。没有卡点就说没有。
2. 人说"开始执行"或在等结果时：
   - 先确认调度器在跑（`bin/orch list --project DATUMOJ` 有进展，或问人）；
   - 再在后台挂上 `bin/orch attention --project DATUMOJ --wait --timeout 1800`。
   - 它一退出就把变化告诉人，然后重新挂上。退出码 3 表示到时没有变化，直接重新挂上。

### 计划与任务
- 做计划、拆任务按 `docs/PLANNING.md`。任务文件念给人确认后才写进 `.orch/tasks/`。
- **`.orch/` 被 `.gitignore` 忽略，任务文件不提交。** 调度器直接读工作区里的 `.orch/tasks/`，并且不停往 `.orch/` 写状态；
  如果被 git 跟踪，每次合并都会被当成"有未提交改动"拦下。不要把 `.orch/` 从 `.gitignore` 里去掉。
- 代码、`docs/PLAN.md` 等其他文件照常提交。
- 写好先校验：`bin/orch validate .orch/tasks`，然后 `bin/orch sync`（调度器在跑时也会自己同步）。

### 不要做
- 不替人批准、否决、修改；人没说清楚就问。
- 不在本会话里运行 `bin/orch run`（嵌套的 claude 会拒绝启动；要人在普通终端里跑）。
- 不改调度器仓库里的任何文件。例外：初始化时、人确认后，在 `projects.yaml` 里加本项目的那一项。
- 不直接改 `.orch/` 下调度器维护的文件（`decisions.md`、`rounds/`、`experts/`）和状态库，一律用 `bin/orch` 命令。
- 调度器在跑时，不在本仓库留没提交的改动（`.orch/` 除外）。要改代码就先提交，或者等任务合并完再改。
