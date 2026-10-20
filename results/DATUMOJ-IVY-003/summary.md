# DATUMOJ-IVY-003 完成说明

## 实现

- `decode_bytes` 处理 UTF BOM、UTF-8、CP932/Shift_JIS 与 EUC-JP；声明编码无法解码时改为探测并设置 `mismatch=True`，BOM 与声明冲突时也标记不符。
- `normalize` 执行 NFKC、空白折叠和零宽字符清除。
- `parse_number` 返回 `Decimal`，支持全角数字、合法千分位及 `△`/`▲` 负数；注记和无法解析的输入返回 `(None, 原始文本)`，不补零。
- `WarekiDate` 保留年月日精度（未给出的字段为 `None`）和年度标记；`WarekiYear` 返回西历年份及 `is_fiscal`。支持令和、平成、昭和、大正、元年、全角数字、汉数字和年号边界校验。
- `kansuji_to_int` 支持连续汉数字及十、百、千、万、亿、兆单位；`article_number` 将条号转为下划线分隔形式。

## 验收与边界

- 验收命令：`make check T=tests/text/`，结果：125 passed。逐次输出见本目录 `test-output.log`。
- 测试只使用内存中的文本和字节，不访问网络，也未读取或修改 fixtures。
- 本任务实现独立文本工具，不实现 `core/contracts.py` 中的协议，因此没有对应的契约合格测试可运行；未导入其他开发线实现。
- 变更文件均在任务 scope：
  - `src/datumoj/text/__init__.py`
  - `tests/text/test_japanese_text.py`
  - `results/DATUMOJ-IVY-003/summary.md`
  - `results/DATUMOJ-IVY-003/test-output.log`

## 未做与风险

按任务要求未做分词、翻译和 CSV。若字节在声明编码下可成功解码但实际内容仍是乱码，仅凭字节无法可靠判定声明错误；当前按要求优先接受成功的声明编码。
