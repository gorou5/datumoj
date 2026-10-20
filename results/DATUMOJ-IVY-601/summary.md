# DATUMOJ-IVY-601 完成说明

## 变更文件
- `src/datumoj/pipeline.py`（新）：`PipelineDeps`、`ParseSummary`、`parse_pending`、`reindex`
- `tests/test_pipeline.py`（新）：9 个测试，全部用假实现和测试内定义的假解析器，离线

## 对上次打回的修正
上次在选解析器之前按 sha256 去重，导致同一内容、不同 `content_type` 的第二个快照不会走到它自己的解析器。
现在流程是：先 `select_parser` 选解析器，再用 `get_parse(sha256, parser.name, parser.version)` 判断是否已处理。
`test_same_sha_different_content_type_runs_each_parser` 覆盖这种情况。

## 行为要点
- 只处理 `outcome=ok` 且有 sha256 的快照，按抓取时间升序，所以同一文档最后写入的是当前版本。
- 失败（含 `check_against` 不一致、raw 缺失）记 `failed` 和原因，继续处理其他快照。同一解析器版本下不重试，升版本才重解析。
- 没有匹配解析器：记 `NO_PARSER` / 版本 `-` / `no_parser`。之后新增了解析器，会走正常解析，不受这条记录影响。
- 有正文块则 `set_content_state(snapshot.resource_url, content_fetched)`，有观测值则 `observations_loaded`。
- `reindex(deps, source_id=None)` 的第一个参数是 deps，返回重新索引的块数。

## 跳过和风险
- 没核对真实解析器和真实存储，只用了假实现。
- 若同一文档的内容回到更早的 sha（A→B→A），第三次因已有解析记录而跳过，当前版本仍是 B。契约没有定义这种情况，未处理。
- 分发过程不是事务：解析成功后写库中途失败，会记 failed，但已写入的部分保留。
- `reindex` 按来源重建时只删除当前块所属的文档再重加，不清理索引里已不在当前版本的残留文档。
