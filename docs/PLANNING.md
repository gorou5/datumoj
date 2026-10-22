# 做计划、拆任务

## 1. 计划写进 `docs/PLAN.md`
和人聊清楚以下几点，写下来，念给人确认：
- 目标：做成什么样算完成；
- 不做什么；
- 里程碑：每个里程碑结束时人要看到什么。

## 2. 拆成任务
每个任务一个文件：`.orch/tasks/<前缀>-<序号>.md`，从 `docs/templates/task.md` 复制。ID 必须以 `<前缀>-` 开头，否则同步时会被拒绝。

**拆的原则：**
- **一个任务一个可自动验证的改动。** 验收命令（`acceptance.automated`）由调度器在全新的 checkout 里亲自运行，退出 0 才算过。
  说不出验收命令的任务，说明还没拆清楚。
- **先有测试再有实现。** 常见做法是把测试和实现放在同一个任务里，正文写清楚要测什么。也可以先单独写一个"写测试"的任务，后面的实现任务把那些测试列进 `protected`（实现者不能改）。
- **scope 要窄。** 只列这个任务该动的路径 glob。改了 scope 外的文件，验收直接失败（`scope_violation`）。`results/<ID>/**` 一般要加上，供 agent 写完成说明。
- **依赖写在 `depends_on`。** 被依赖的任务完成并合并后，下一个任务才会从合并后的代码开始。互不依赖的任务会并行（受项目配额 `max_concurrent` 限制）。
- **大小**：一个 agent 一次能做完、半小时左右能验证。太大就拆。

**字段选择：**
- `runner`：`claude` 或 `codex`。
- `tier`：`low` / `medium` / `high`，按难度选，对应不同的模型和推理强度。
- `acceptance.review.by`：
  - `other_family`：做完由另一家模型自动审查，通过后自动合并（推荐）；
  - `human`：等人 `approve`；
  - `none`：验收过了直接合并。
- `max_attempts`：失败几次后转 blocked 等人，一般 2。
- 验收命令要用调度器进程能找到的程序。项目有自己的虚拟环境时，写绝对路径，例如 `<项目目录>/.venv/bin/pytest -q tests/test_x.py`。

## 3. 确认与提交
1. 把任务列表（ID、标题、依赖、验收命令）念给人确认。
2. `bin/orch validate .orch/tasks`，有错就改。
3. `git add docs/PLAN.md && git commit -m "计划与第一批任务"`。任务文件在 `.orch/` 里，被忽略，不提交。
4. `bin/orch sync`，然后 `bin/orch list --project <项目名>` 确认任务都进来了（依赖已满足的会是 ready）。

## 4. 执行中
- 任务进展：`bin/orch list --project <项目名>`。
- 每次尝试的证据包：调度器目录下 `results/<ID>/attempt_<N>/evidence.md`。人问为什么失败时，读它转述。
- 卡点（失败转 blocked、等人审查、合并被拦）都会出现在 `attention` 里，按 AGENT-GUIDE 的表格处理，每一步都要人说了才执行。
- 计划要调整时：还没开始的任务直接改文件（调度器会同步）；已经 done 的任务不改，另开新任务。
- 想法还没定、需要专家论证的，走议题：`bin/orch propose --project <项目名> --file <卡片>`，见 AGENT-GUIDE 第 4 节。
