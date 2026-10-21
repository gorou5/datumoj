# DATUMOJ-IVY-505 完成说明

## 实现

- `HybridRetriever` 新增可配置参数 `min_vector_score`，默认值为模块常量 `DEFAULT_MIN_VECTOR_SCORE = 0.3`。
- 向量结果在 RRF 融合前按原始相似度分数过滤；分数等于阈值时保留。关键词结果完全不经过该阈值。
- 当低分向量结果被忽略且最终没有有效命中时，保留原有缺口状态判定，并增加警告“向量近邻相似度过低，已忽略”。
- 默认阈值是临时起点；Lance 向量索引的分数为 `1 - distance`，当前采用 0.3 过滤明显偏低的近邻分数。该值将在 H5 使用 bge-m3 和评测集校准。

## 验收

- `make check T=tests/index/test_hybrid_threshold.py`：4 passed。
- `make check T=tests/index/test_hybrid.py`：54 passed，包含原有 HTTP 503 缺口检查；出现 2 条 LanceDB `table_names()` 弃用警告。
- 沙箱中 uv 不可用，`make check` 按仓库脚本提示使用既有 `.venv` 完成测试。
- 详细输出见同目录的 `test-hybrid-threshold.log` 和 `test-hybrid.log`。

## Review checklist

- 关键词命中不受阈值影响：过滤逻辑只应用于向量结果；新测试确认关键词命中保留且只标记 `keyword`。
- 只有低于阈值的向量结果时返回 `zero_hits`：新测试确认，并检查指定警告。
- 阈值可配置、默认值来由明确：新测试覆盖自定义阈值；常量注释及本文说明临时值和 H5 校准安排。
- 原有 503 测试仍通过：受保护的 `tests/index/test_hybrid.py` 全部 54 项通过。
- 越界检查：只改任务 scope 中的实现、测试和结果说明文件；未改保护文件或契约。

## 变更文件

- `src/datumoj/index/hybrid.py`
- `tests/index/test_hybrid_threshold.py`
- `results/DATUMOJ-IVY-505/summary.md`
- `results/DATUMOJ-IVY-505/test-hybrid-threshold.log`
- `results/DATUMOJ-IVY-505/test-hybrid.log`

跳过了什么：未修改向量索引、契约或依赖。未核对 H5 评测集校准；默认阈值仍是临时值。风险：bge-m3 的实际分数分布尚未校准。
