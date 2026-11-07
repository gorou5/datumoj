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

## 公的相談・通報窓口（2026-11-07 追加）

| Dataset | Rows | Source | Level | Notes |
|---|---|---|---|---|
| `government_number` 追加（警察庁・総務省） | 2 rows | 警察庁 SOS47 公式ページ／でんわんセンター公式サイト | 已证明 | 0120-924-839（警察庁「匿名通報ダイヤル」）は SOS47 ページに番号・報奨金制度の掲載を確認。03-6162-1111（でんわんセンター）は公式サイトに番号・受付時間（平日10時〜17時）・相談無料の掲載を確認し、事業主体＝総務省（請負事業）、受託運営＝一般社団法人日本ユニファイド通信事業者協会（JUSA）は同サイト「センターについて」で確認。`verified_on` は行ごとに保持（build_gov_seed.py が entry の `verified_on` を優先）。 |
| `business_registration`（seed/business_seed.sql） | 1 row | 警察庁 SOS47 公式ページ | 已证明 | 0120-344-999（日本証券業協会 未公開株通報専用窓口）。同協会は政府機関ではないため government_number には入れず、事業者オーバーレイに登録。 |
