# Rikolto 本地入口

在仓库根目录安装锁定的依赖并启动 Rikolto：

```bash
uv sync --frozen
uv run --frozen rikolto --help
uv run --frozen rikolto mcp
```

Python 模块入口位于 `datumoj.rikolto`；项目包名仍为 `datumoj`。
Claude Code 中的 MCP 注册名改为 `rikolto`，在仓库根目录重新注册：

```bash
claude mcp remove datumoj
claude mcp add rikolto -- uv run --frozen rikolto mcp
```

回答规则位于 `skills/rikolto/SKILL.md`，MCP 工具名和参数保持不变。
合并后在原本的本机工作目录执行 `uv run --frozen rikolto schedule launchd install`；
安装会先卸载旧标签 `com.datumoj.schedule`，再加载 `com.datumoj.rikolto.schedule`。
核对 JMA 抓取没有缺口后，才能删除为这次切换暂留的 `datumoj` 命令别名。

# 初始化（第一次对话做一次）

人会给出：项目名（英文字母开头，如 `notes`）和任务前缀（大写字母开头的大写字母或数字，如 `NT`）。缺哪个就问。
命令都在仓库根目录用 `bin/orch …`（调度器入口的包装脚本）。

1. **填占位符**：把 `CLAUDE.md`、`docs/PLAN.md` 里的 `{{PROJECT}}`、`{{PREFIX}}` 换成实际值，删掉 `CLAUDE.md` 里的初始化注释。
2. **建仓库**：还不是 git 仓库时，运行 `git init -b main`；然后 `git add -A && git commit -m "从模版初始化"`。
   调度器要求仓库至少有一个提交。
3. **注册到调度器**：
   - 要加的一项（缩进与 `<调度器仓库>/projects.yaml` 现有项一致）：
     ```yaml
     - name: <项目名>
       repo: <本目录的绝对路径>
       priority: 2
       quota: {max_concurrent: 1, mem_gb: 8}
       id_prefix: <前缀>
     ```
   - 先把它念给人确认，人同意后再追加到 `projects.yaml` 末尾。名字和前缀不能和已有项目重复。
   - 这是唯一允许改的调度器文件。
4. **建骨架**：运行 `bin/orch project init <项目名>`，生成 `.orch/`（包括 `DIRECTION.md`、`roster.yaml`、`decisions.md`、`tasks/`）。
   `.orch/` 已在 `.gitignore` 里，不提交（原因见 CLAUDE.md"计划与任务"）。
5. **验证**：
   - `bin/orch list --project <项目名>`：不报错（这时还没有任务）；
   - `bin/orch attention --project <项目名>`：输出"没有等你处理的事"或列出卡点。
6. **（可选）方向与红线**：`.orch/DIRECTION.md` 写项目方向和禁区，供专家提议、评审时参考。只在走议题（`propose`）时用到，直接写任务可以先不填。
   改的时候和人一起写，再用 `bin/orch direction set --project <项目名> --file <文件>` 提交，不直接编辑。
7. 告诉人初始化完成。提醒人：执行任务前要在普通终端里启动 `orch run`（命令见 README.md）。
