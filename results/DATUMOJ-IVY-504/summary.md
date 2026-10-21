# DATUMOJ-IVY-504 完成说明

## 改动

- `src/datumoj/index/fts.py`：规范化后按空白拆词；长词以转义后的 FTS trigram 短语 `OR` 检索，短词通过有条数上限和超时的 LIKE 扫描参与召回。合并结果按命中不同查询词数量降序，再按原 FTS BM25 标题加权分数（LIKE 结果沿用出现次数分数）排序。单词查询保留原路径。
- `tests/index/test_fts_multiword.py`：新增 13 个日文块的人工样本，覆盖不相邻的部分命中、词数排序、全角空格、短词混合与纯短词查询、`"` / `AND` / `*` 字面查询和来源过滤。

## 验收

- `make check T=tests/index/test_fts_multiword.py`：5 passed。
- `make check T=tests/index/test_fts.py`：65 passed，原有 FTS 测试通过。
- 完整输出见同目录 `make-check-test_fts_multiword.log` 与 `make-check-test_fts.log`。
- FTS 词项都独立加引号，内部双引号加倍；短词使用 LIKE 转义。来源过滤应用于 FTS 和 LIKE 查询。
- 变更文件仅限任务 scope：实现、指定的新测试、此结果目录中的说明与日志。

跳过：未运行任务以外的测试或基准。未核对真实法令语料上的 Recall@10；多词 FTS 需要收集所有 OR 命中后排序，宽泛查询可能比原先单词查询耗用更多内存。
