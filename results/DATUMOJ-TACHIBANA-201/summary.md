# DATUMOJ-TACHIBANA-201 完成说明

## 修改文件

- `src/datumoj/parsers/pdf.py`：新增 `gov_web` / `application/pdf` 解析器。按页提取并用 `datumoj.text.normalize` 规范化正文；短文本页跳过并标记 `needs_ocr`；生成稳定 doc_key、父页关系、发布日期和页数元数据；将加密、损坏和不可读输入转为 `ParseError`。
- `tests/parsers/test_pdf.py`：继承 `ParserSuite`，并覆盖真实文本 PDF、合成扫描件、加密件、截断件、标题回退、页定位、元数据和同 URL 稳定性。
- `results/DATUMOJ-TACHIBANA-201/test-output.log`：保存验收输出和首轮失败修正记录。
- `results/DATUMOJ-TACHIBANA-201/summary.md`：本完成说明。

## 实现方案

直接用锁定的 pypdfium2 提取每页文本，不增加依赖。每页规范化后去掉空白计算字符数；少于 50 字符的页不生成 Chunk，逐页写 warning，并在全页都短时补充 `needs_ocr all`。PDF 标题依次取有效元数据 Title、第一页首条有意义文本、URL 文件名。doc_key 使用小写 scheme/host、去默认端口和 fragment 后的 URL 计算 SHA-1 前 16 位。没有尝试解密；PDFium 的密码错误映射为含 `encrypted` 的 `ParseError`，其他读取错误映射为损坏/不可读 `ParseError`。

没有实现 OCR、表格提取或采集器接线，符合任务“不做”范围。

## 验收与复核

- `make check T=tests/parsers/test_pdf.py`：通过，10 passed。执行器提示 uv 在沙箱不可用，并按项目配置使用 `/Users/aaron/workspace/datumoj/.venv/bin/python`；详见测试日志。
- Parser 契约合格测试：通过，使用 `datumoj.testing.contract_suites.ParserSuite`。
- 覆盖清单：36 页文本 PDF 与 `page=` 定位；第 25 页短文本 warning；扫描件两页 OCR 标记及 `needs_ocr all`；加密和截断错误；PDF 标题、首行和文件名回退；parent_ref、`published_at` 与无日期；稳定 doc_key。
- 依赖按锁文件执行 `uv sync --frozen`。默认 uv 缓存路径不可写后，使用 `/private/tmp/datumoj-201-uv-cache` 完成安装；未改依赖或锁文件。
- 测试仅读取本地 fixtures，没有联网。实现只依赖契约、模型、`datumoj.text` 和锁定的 pypdfium2；没有导入其他解析器实现。
- 已按评审清单自查 scope、覆盖、契约测试、离线性和导入边界。独立 `other_family` 复核及 human sampled 尚未执行。

## 未验证与风险

未运行全仓库测试或集成流水线；任务验收只要求指定 PDF 测试文件。乱码标题识别采用替换符及常见乱码标记启发式，当前只用任务 fixtures 覆盖，其他编码形态仍需人工抽样复核。

跳过 OCR 和表格抽取；未核对全仓库集成及独立评审。除标题乱码启发式的样本覆盖有限外，没有发现额外风险。
