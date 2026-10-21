# DATUMOJ-IVY-705 完成说明

## 变更

- 仅给 `stat-estat-tokyo-ict-employees-2016` 增加 `legal_org_code = keiso4A.0000` 条件，`equals` 保持 `849374`；说明该表同一指标还会解析出五个经营组织分项。
- 统计题 SQL 测试现在以登记表元数据和原始东京 CSV 调用真实 `EstatTableParser`，将整份解析结果写入 `DuckDBObservationStore` 后执行 SQL。新 SQL 唯一返回 `849374`；移除经营组织条件的旧 SQL 返回 6 行，其中包含 `849374`。
- 其他统计题定义未改动。变更文件：`eval/questions/stats.yaml`、`tests/eval/test_questions_stats.py`、`results/DATUMOJ-IVY-705/check.log`、`results/DATUMOJ-IVY-705/summary.md`。

## 验收

原样执行 `make check T=tests/eval/test_questions_stats.py`：5 项通过。完整输出见 `check.log`。`git diff --check` 通过。

跳过：无。未核对：人工抽样评审尚未执行。风险：无已知风险。
