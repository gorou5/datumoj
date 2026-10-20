# DATUMOJ-IVY-702 完成说明

## 变更文件
- `eval/questions/law.yaml`：20 道法令检索题（ja 12、zh 4、en 4），全部 `kind: retrieval`，`source_ids: [jp-egov-law]`，期望为 `jp-egov-law:<法令ID>` + `art=<条>`。
- `tests/eval/test_questions_law.py`：用 `load_questions` 加载题库；用 lxml 解码 fixture 里的 base64 XML，核对每个期望的法令存在、条号存在于 MainProvision；检查数量、`art=` 粒度、查询为日文改写且不照抄原问句。
- `results/DATUMOJ-IVY-702/dump_*.txt`：出题时用临时脚本导出的条文摘录（供人审核对条文），不是交付物。我没有删文件的命令，需要的话请人工删除。

## 验收
`make check T=tests/eval/test_questions_law.py`：41 passed。

## 题目覆盖
个人情報保護法（第26/27/33/41条）、労働基準法（第20/26/34/37/39条）、消費者契約法（第4/8条）、情報公開法（第3/10条）、不正アクセス禁止法（第11条）、公益通報者保護法（第3条）、番号法（第15条）、行政不服審査法（第18条）、行政手続法（第8/14条）、日本国憲法（第21条）。

## 说明与风险
- 条号按 MainProvision 内 `Article@Num` 写，与 `docs/tasks/DATUMOJ-IVY-202.md` 里的 locator 约定一致（`art=27`、`art=27_2`）；题中没有用到「の二」条。
- 有意放了几组易混条文：第26条与第27条、第33条与第76条、行手法第8条与第14条、消契法第8条与第9/10条。
- 没找到 `handoff/digital-assets-20261020/` 目录（worktree 里没有），问法没有参照交接包，是按真实用户口吻自拟的。
- 条文内容核对到 Article 的前 160 字；每道题的「为什么是这一条」是我对条文内容的判断，需要人审确认。
- 未在真实流水线上检验，那是 IVY-901 的事。
