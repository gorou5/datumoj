# DATUMOJ-IVY-704 完成说明

## 完成内容

- 新增 20 道行为题，使总题数达到 60；覆盖四种缺口、时间语义、英文与中文提问、注入 containment，以及法令与统计跨来源检索。
- 新增 `jp-eval-noingest`、`jp-eval-fetchfail`、`jp-eval-injection` 的 SourceSpec fixture。Manifest seed 只含一条 HTTP 503 失败快照；noingest 无快照；unknown 未注册。
- 新增人工构造的市民公告，其中含“忽略之前的指示”文字，并记录 DATUMOJ-IVY-903 装配三种缺口状态和注入样本的方式。
- 新增回答规则 skill：七条规则、个案法律和补贴资格边界、三个完整示例对话。
- 行为测试核对题数与类别、缺口来源及状态、评测 SourceSpec 和失败快照、JMA feed entry/doc_key/locator、法令条文、样本文档键、双语日文子查询，以及 skill 内容。

## 验收

执行 `make check T=tests/eval/test_questions_behavior.py`，结果 **7 passed**。沙箱中 `uv` 不可用，`make check` 按项目规则自动使用只读的主仓库 `.venv`。完整输出见 [acceptance.log](acceptance.log)。

## 变更文件

- `eval/questions/behavior.yaml`
- `tests/fixtures/eval_extra/README.md`
- `tests/fixtures/eval_extra/sources/jp-eval-noingest.yaml`
- `tests/fixtures/eval_extra/sources/jp-eval-fetchfail.yaml`
- `tests/fixtures/eval_extra/sources/jp-eval-injection.yaml`
- `tests/fixtures/eval_extra/manifest-seed.yaml`
- `tests/fixtures/eval_extra/announcement-injection.synthetic.txt`
- `skills/datumoj/SKILL.md`
- `tests/eval/test_questions_behavior.py`
- `results/DATUMOJ-IVY-704/acceptance.log`
- `results/DATUMOJ-IVY-704/summary.md`

未运行集成任务 903。受“只依赖契约”约束，测试用 `SourceSpec` 校验注册表 YAML，没有直接导入 `datumoj.core.registry`；集成装配仍需按 README 将 seed 快照写入 Manifest。
