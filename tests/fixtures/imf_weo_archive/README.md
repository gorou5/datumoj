# imf_weo_archive：IMF WEO 2009—2010 存档节选

供 DATUMOJ-WISTERIA-310（旧版 WEO 数据库文件 → `import-release` 历史发布）离线测试使用。定稿见 `docs/plans/0.4-wisteria-sources.md` 2.1、第 0 节第 2 项。

- 来源：负责人 2026-11-19 在浏览器从 IMF WEO Database 存档页手动下载四版 "By Countries" 整表（IMF 网站对脚本 403，不程序化下载）。
- 节选：每个文件只留表头、希腊两行、科特迪瓦一行（`NGDPD`，国名含 cp1252 的 `0xF4`，用来测编码），以及原文件数据之后的空行和出处行。**保留的每一行都是原文件的原始字节**，没有改编码、换行或数值。
- 原文件的大小、SHA-256、下载页在 `_sources.json`。完整性校验：本目录运行 `shasum -a 256 -c SHA256SUMS`。

## 文件格式（四版一致）

- 扩展名是 `.xls`，实际是制表符分隔的文本，CRLF 换行，编码 cp1252（不是 UTF-8）。
- 列：`WEO Country Code`、`ISO`、`WEO Subject Code`、`Country`、`Subject Descriptor`、`Subject Notes`、`Units`、`Scale`、`Country/Series-specific Notes`、逐年数值（2009 年两版到 2014，2010 年两版到 2015）、`Estimates Start After`。
- `WEO Country Code` 是 IFS 数字码（希腊 174），国家用 `ISO` 列。
- 字段偶尔带双引号（含逗号的注释）；空值是空字符串。
- 数据之后是空行和一行出处，如 `International Monetary Fund, World Economic Outlook Database, April 2010`；有的版本整行加引号并带尾随制表符，有的没有，结尾有没有换行也不一样。

## 希腊一般政府赤字（占 GDP %）

| 节选文件 | 版本 | 指标代码 | 2008 | 2009 | 2010 | Estimates Start After |
|---|---|---|---|---|---|---|
| `weo_2009_apr_GRC_excerpt.tsv` | 2009-04 | `GGB_NGDP` General government balance | -3.748 | -4.509 | -5.206 | 2007 |
| `weo_2009_oct_GRC_excerpt.tsv` | 2009-10 | `GGB_NGDP` | -5.020 | -6.398 | -7.110 | 2008 |
| `weo_2010_apr_GRC_excerpt.tsv` | 2010-04 | `GGXCNL_NGDP` General government net lending/borrowing | -7.751 | -12.866 | -8.717 | 2009 |
| `weo_2010_oct_GRC_excerpt.tsv` | 2010-10 | `GGXCNL_NGDP` | -7.7 | -13.584 | -7.882 | 2009 |

- 同一行还有本币值的 `GGB` / `GGXCNL`（十亿欧元）。
- **代码变更**：2009 年两版用 `GGB_NGDP`（General government balance），2010-04 起改为 `GGXCNL_NGDP`（net lending/borrowing，GFSM 2001）。负责人 2026-11-20 定：**保留原代码，显式写明对应关系**，不在解析时改代码。
- `Estimates Start After` 之后的年份是估计或预测：2009 年两版里的 2009 年值都是预测。

## 导入参数（由人给出，不从文件名推断；已核对）

| 版本 | `release_id` | `released_at`（负责人 2026-11-20 核对） |
|---|---|---|
| 2009-04 | `WEO-2009-APR` | 2009-04-22 |
| 2009-10 | `WEO-2009-OCT` | 2009-10-01 |
| 2010-04 | `WEO-2010-APR` | 2010-04-21 |
| 2010-10 | `WEO-2010-OCT` | 2010-10-06 |

## 完整文件才有的情况（`*_mixed_excerpt.tsv`，供 WISTERIA-313）

2026-11-20 云端预演发现，完整文件里有四个 GRC 节选覆盖不到的情况。从 2009-04 和 2010-10 两版各取 6 行原始字节（行号见 `_sources.json` 的 `original_lines`），连同原文件末尾的空行和出处行：

- `Scale` 为 `Units`（如 `NGDPPC` 人均 GDP 本币，每版约 730 行），按 1 处理；
- 数值 `n/a`（每版 1 万至 3.9 万个）和 `--`（约 400 个），都是空值；
- `Estimates Start After` 为 `0`（每版 27—47 行），表示未知；
- 计划外的序列（`JPN NGDPD`、`GRC LUR` 等）：导入只取计划内的希腊 `GGXCNL_NGDP` / `GGB_NGDP`（负责人 2026-11-20 定）；
- 数据后有一行全是制表符的空行（2010-10）。

## 许可与署名

- IMF Copyright and Usage（<https://www.imf.org/en/About/copyright-and-terms>）：署名后可复制、派生、再分发，不得改动数值；本项目按非商业使用（2026-11-16 负责人定）。
- 署名：International Monetary Fund, World Economic Outlook Database, April 2009 / October 2009 / April 2010 / October 2010（文件自带的出处行）。
