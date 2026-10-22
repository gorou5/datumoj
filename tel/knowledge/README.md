# 基礎知識ライブラリ（総務省「電気通信番号指定状況」）

tel.datumoj.com の**基礎庫**。総務省が「電気通信番号計画（令和元年総務省告示第6号）第1第4項」に基づき公表する指定状況ページを、2026-10-22 にダウンロードして保存したもの。以後のシード改訂は、二次編集物ではなくこの基礎庫を一次資料として行う。

- 出典ページ：https://www.soumu.go.jp/main_sosiki/joho_tsusin/top/tel_number/number_shitei.html
- ページ自体のデータ時点：令和8年10月1日現在（ファイルごとに時点が異なる。下表の `data_asof` 参照）
- 取得日：2026-10-22
- 保存物：
  - `soumu_number_shitei.html` — ページ原文（Shift_JIS）
  - `soumu_number_shitei.txt` — テキスト抽出
  - `soumu_fixed_number_structure.png` — 固定電話の番号構成図（同ページ掲載図。国内プレフィックス「0」＋市外局番1〜4桁＋市内局番1〜4桁［計5桁］＋加入者番号4桁）
  - `soumu_excel/` — ページが配布する Excel 全27ファイル（原本のまま）
  - `soumu_foundation.db` — **基礎庫本体（SQLite、生成物）**。`../scripts/import_soumu_foundation.py` で再生成できるため Git には入れない。

## 基礎庫の設計

- **1ファイル＝1テーブル**。ファイル自身の表構造をそのまま取り込む（タイトル行を除き、ヘッダー行を検出、全て TEXT で格納）。整形・解釈はここではしない（ステージング層）。
- テーブル目録と出典は基礎庫内の `source_file` テーブルに記録（table_name / source_file / category_ja / sheet_name / data_asof / row_count / page_url / fetched_on）。
- D1（tel_datumoj）の精選テーブルは、この基礎庫から派生させる。特に：
  - 固定電話9ファイル（計41,987行）は「番号区画コード（＝MA）・番号（0＋市外局番＋市内局番）・事業者・使用状況」を持ち、現行シード（Wikipedia 由来の MA 表）を公式データで置き換える材料になる。
  - 携帯・050・0120 等の行列形式ファイルは、セル＝6桁プレフィックス区画×事業者。展開（アンピボット）すれば区画→事業者の長形式が得られる。

## テーブル一覧（27表・49,652行）

| テーブル | 内容 | 行数 | データ時点 |
|---|---|---:|---|
| designation_fixed_area_leading_1 〜 _9 | 固定電話番号（市外局番の先頭数字別） | 41,987（計） | 令和5〜8年（ファイル別） |
| designation_mobile_060 / _070 / _080 / _090 | 音声伝送携帯電話番号（行列形式） | 各90 | 令和6〜8年 |
| designation_m2m_020_11digit / _14digit | データ伝送携帯電話番号（020） | 80 / 100 | 令和8年10月 |
| designation_ip_phone_050 | 特定IP電話番号（050） | 900 | 令和8年6月 |
| designation_toll_free_0120 / _0800 | 着信課金機能 | 各100 | 令和6〜7年 |
| designation_navi_dial_0570 | 統一番号機能（0570） | 100 | 令和8年5月 |
| designation_charge_billing_0990 | 情報料代理徴収機能（0990） | 100 | 令和3年9月 |
| designation_special_connection | 特定接続電話番号 | 1,000 | 令和3年9月 |
| designation_imsi | IMSI | 1,000 | 令和8年9月 |
| designation_carrier_equipment_id | 事業者設備識別番号 | 27 | 令和7年5月 |
| designation_supplementary_service_id | 付加的役務識別番号 | 578 | 令和8年10月 |
| designation_emergency_numbers | 緊急通報番号（110／118／119） | 20 | 令和7年7月 |
| designation_international_signalling_id | 国際信号局識別番号 | 72 | 令和8年8月 |
| designation_data_equipment_id | データ通信設備識別番号 | 3,128 | 令和3年5月 |

## 再生成

```
python3 scripts/import_soumu_foundation.py   # openpyxl / xlrd が必要
```
