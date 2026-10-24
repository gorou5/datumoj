# Seed data sources & evidence

Collected 2026-10-22. Assertion levels follow repo AGENTS.md §3.3.

| Dataset | Rows | Source | Level | Notes |
|---|---|---|---|---|
| `area_ma.json` → `area_code` / `ma` / `ma_area` | 561 MA rows, 368 area codes | Public 番号区画 (numbering-zone) table as republished in Wikipedia 「日本の市外局番」, parsed by `scripts/fetch_area_ma.py` (kept in build notes) | 推导・待验证 | The underlying 番号区画 designations are official (MIC/NTT), but this copy is a secondary compilation. v2 replaces it with a direct parse of MIC/NTT official PDFs. |
| Band facts in `alloc_block` (090/080/070/0705/0706/060/050/0120/0800/0570/020) | 11 rows | MIC 電気通信番号使用状況 publications + 2024-12-20 numbering-plan change (060) | 已证明（帯レベル） | Carrier-level block designation is deliberately NOT seeded; `carrier_id` stays NULL until MIC designation PDFs are parsed. |
| `tp_source` registry | 6 rows | Publisher sites + 2026-10-22 field research (see design doc §5.3) | 已证明 | Registry only — no `tp_flag` rows are seeded in v1. |

Design: `~/workspace/tel-datumoj/DESIGN.md` (working copy; to be moved into `docs/` by a later PR).

## 詳細区画（2026-10-22 基礎庫由来に格上げ）
- 固定電話の6桁区画（23,106件）と携帯・050・0120・0800・0570・020の事業者別区画（7,332件）は、総務省「電気通信番号指定状況」の公式Excelから起こした基礎庫（knowledge/soumu_foundation.db）由来。生成は scripts/build_official_seed.py。
- 固定区画は番号区画コード（MA）を持ち、番号ページでは該当MAを1つに特定して表示する。
- MAの名称・対象地域テキスト自体は引き続き Wikipedia「日本の市外局番」由来の二次編集だが、区画コード↔市外局番の対応は公式ファイルと全数照合し不一致ゼロ（561件）だった。
