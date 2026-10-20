# DATUMOJ-IVY-302 完成说明

## 实现

- 在 `datumoj.reference` 导出地区记录、查询、标签归一化和五位/六位代码转换。
- 按总务省规则校验六位码末位；五位码转换会保留前导零。全国 `00000` 有独立记录。
- 从仓库内令和 6 年 1 月 1 日版 Excel 离线读取两张工作表，按六位码去重；名称查询接受都道府县简称、全角/空白变体及都道府县与市区町村连写形式，同名名称返回全部候选。
- 查不到或格式/校验位无效的代码返回空候选；转换函数对格式或校验位错误抛出 `ValueError`。

## 验收

- 命令：`make check T=tests/reference/`
- 结果：通过，5 passed。完整输出见 `test.log`。
- 测试覆盖校验位双向转换、47 个都道府县代码、东京简称及 2/5/6 位查找、府中市/伊達市/泊村同名候选、跨表政令市区、全国码、无效代码，以及运行时 Excel 与只读 fixture 字节一致。
- 测试无网络请求；实现只依赖标准库、已锁定的 `openpyxl` 和本任务内模块，没有导入其他开发线实现。

## 交叉评审状态

- 本 worktree 未执行 `other_family` 独立复核；请在调度器评审阶段按任务 checklist 核对。
- 契约合格测试不适用：本任务没有实现接口协议。

## 变更文件

- `src/datumoj/reference/__init__.py`
- `src/datumoj/reference/areas.py`
- `tests/reference/test_areas.py`
- `results/DATUMOJ-IVY-302/test.log`
- `results/DATUMOJ-IVY-302/summary.md`

不实现市町村合并沿革或坐标；码表仍固定于随仓库提供的令和 6 年 1 月 1 日版，更新维护不在本任务范围。
