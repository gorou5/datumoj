# 项目模版（接入 orchestrator 调度器）

每建一个新项目，复制这个目录，再开一个对话完成初始化。这份 README 是给人看的；对话里的 Claude 读 `CLAUDE.md` 和 `docs/`。

## 用法
1. 复制模版（`-R` 会连同隐藏文件一起复制）：
   ```bash
   cp -R /Users/aaron/workspace/orch-project-template /Users/aaron/workspace/<新项目>
   ```
2. 在新目录里开 Claude Code 对话，第一句说：
   > 按 docs/SETUP.md 初始化这个项目。项目名 `<英文名>`，任务前缀 `<大写字母>`。

   Claude 会做这些事：
   - 填好占位符，`git init` 并提交；
   - 把要加进调度器 `projects.yaml` 的那一项给你看，你确认后才写；
   - 运行 `orch project init`，然后验证调度器能看到这个项目。
3. 之后在同一个对话（或以后任何一次对话）里：
   - 说"我们来做计划"：Claude 按 `docs/PLANNING.md` 把计划写进 `docs/PLAN.md`，再拆成 `.orch/tasks/` 下的任务文件，念给你确认（任务文件不进 git，计划文档照常提交）。
   - 说"开始执行"：确认普通终端里 `orch run` 在跑（Claude 不能在对话里启动它），Claude 在后台盯着卡点，有变化就告诉你。
   - 对话重开后说"现在什么情况"：Claude 先跑 `attention`，状态都在调度器里，不用记得之前聊过什么。

## 调度器要单独在普通终端里跑（全部项目共用一个，只开一个）
```bash
/Users/aaron/workspace/orchestrator/.venv/bin/python -m orch --config /Users/aaron/workspace/orchestrator/orch.yaml run
```
- 它会处理 `projects.yaml` 里注册的所有项目，不要每个项目各开一个。
- 不能在 Claude Code 会话里运行：嵌套的 `claude` 会拒绝启动。

## 文件
- `CLAUDE.md`：项目规则，以及"怎么替人操作调度器"（带占位符，初始化时填好）。
- `docs/SETUP.md`：第一次对话的初始化步骤。
- `docs/PLANNING.md`：怎么做计划、拆任务、写验收。
- `docs/PLAN.md`：计划本身（初始化后由对话填写）。
- `docs/templates/task.md`：任务文件模版。
- `bin/orch`：调用调度器的包装脚本（里面写着调度器路径和配置；换配置只改它）。
- `.gitignore`：忽略 `.orch/`。这一项必须保留，调度器在里面不停写状态，被 git 跟踪会让合并全被拦下。
- `.orch/`：不在模版里，由 `orch project init` 生成；任务文件也在这里，不进 git。
