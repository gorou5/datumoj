# DATUMOJ-IVY-706 完成说明

## 实现

- 保留文档检索前 3 条引用中的期望值判定。
- 为 `evaluate` 增加可选的现有 `Manifest` 契约注入。SQL 出处行的数值规范化后匹配时，框架按快照哈希、来源和抓取时间查找成功快照，调用 `citation_for_observation` 生成 `Citation`，再调用 `cite`；只有返回证据才算出现。
- 任一来源都没有可还原的期望值时，失败原因明确指出文档引用和 SQL 出处行均未找到。
- 新增 4 项离线测试，覆盖文档命中、可还原 SQL 出处、SQL 数值匹配但出处无法还原、SQL 数值不匹配。

## 验收

- `make check T=tests/eval/test_framework_numeric.py`：4 passed。
- `make check T=tests/eval/test_framework.py`：6 passed，原有 DATUMOJ-IVY-701 框架测试通过。
- 两次执行均提示沙箱中 `uv` 不可用并使用主仓库 `.venv`；测试命令正常通过。
- `git diff --check`：通过。

## 变更文件

- `src/datumoj/eval/framework.py`
- `tests/eval/test_framework_numeric.py`
- `results/DATUMOJ-IVY-706/summary.md`

## 评审检查

- SQL 出处行必须能还原到真实成功快照才计数；`cite` 无证据时不计数。
- 原文档检索前 3 条命中判定保留。
- 数值不等和出处还原失败均保持失败。
- 原有框架测试全部通过。
- 仅修改任务 scope 内文件；未改契约、检索排序、题库或受保护文件。

跳过：无。未核对：未运行任务未列出的完整测试套件。风险：SQL 出处行判定需要调用方为 `evaluate` 提供 `Manifest`；未提供时仅使用文档引用判定。
