# tel — tel.datumoj.com 日本电话番号数据库

日本の電話番号データベース。番号ページはデータベースの区画表からアクセス時に
組み立てて生成する（1番号1行のデータは持たない）。設計書は
`~/workspace/tel-datumoj/DESIGN.md`（作業コピー。後日 `docs/` へ移管予定）。

## アーキテクチャ

- **Cloudflare Worker**（`src/`）+ **D1**（データベース名 `tel-db`）
- 主データは「区画」単位の表のみ：番号計画の形式ルール（号種・桁数・切分・
  プレフィックス帯）から計算で組み立てる。表形式でしか存在しない指定データ
  （市外局番／MA／番号区画）だけを参照表として持つ。
- 索引は3層：Tier 1（実データのある番号ページのみ index）／Tier 2（区画情報
  のみは noindex）／Tier 3（未指定は実在しないページ＝404）。

## ディレクトリ（Cloudflare 標準構成）

```
tel/
├── wrangler.toml        # Worker 設定（custom domain: tel.datumoj.com, D1 binding: DB）
├── migrations/          # D1 マイグレーション（wrangler d1 migrations）
│   ├── 0001_init.sql            # v1 プロトタイプ（履歴として保持）
│   └── 0002_tel_prefix_normalized.sql  # tel_ 接頭辞・正規化・ビュー・政府番号DB
├── seed/
│   ├── area_ma.json     # 番号区画表（561 MA / 368 市外局番）
│   ├── government_numbers.json # 政府機関の公表番号（公式ページで確認したものだけ）
│   ├── seed.sql         # 生成物（scripts/build_seed.py）
│   ├── government_seed.sql     # 生成物（scripts/build_gov_seed.py）
│   └── SOURCES.md       # 証拠・出典メモ
├── scripts/
│   ├── build_seed.py
│   └── build_gov_seed.py
└── src/
    ├── index.js         # ルーター
    └── lib/
        ├── number.js    # 正規化・切分（純関数）
        ├── compose.js   # 拼接パイプライン（v_tel_* ビュー経由）
        └── render.js    # HTML テンプレート
```

## データベース（全テーブル tel_ 接頭辞、DBA 標準の正規化）

| テーブル | 役割 |
|---|---|
| `prefecture` / `area_code` / `ma` | 都道府県・市外局番・単位料金区域（MA） |
| `line_type` / `carrier` / `alloc_block` | 号種・事業者・区画割当（最長プレフィックス一致） |
| `business` / `report` | 事業者登録・利用者報告（オーバーレイ） |
| `tp_source` / `tp_flag` | 第三者機関の標識（出典付き転載のみ、自前判定なし） |
| `gov_agency` / `gov_number` | 政府機関とその公表番号 |

ビュー：`v_ma_detail` / `v_area_summary` / `v_block_detail` /
`v_flag_detail` / `v_gov_number_detail`（アプリ側の手書きJOINを置換）。

## 運用

```bash
# シード再生成
python3 scripts/build_seed.py && python3 scripts/build_gov_seed.py
# マイグレーション＆シード投入（Datumoj アカウントの認証情報を使用）
wrangler d1 migrations apply tel-db --remote
wrangler d1 execute tel_datumoj --remote --file=seed/seed.sql
wrangler d1 execute tel_datumoj --remote --file=seed/government_seed.sql
# デプロイ
wrangler deploy
```

## 方針

- 無料で公開されているリソースのみを使う（商用データベースの契約・照会はしない）。
- 第三者の標識は出典・公表日つきで転載し、本サイトが独自に有害性を判定しない。
- 「割当事業者」は指定時点の情報であり、現契約事業者を断定しない（MNP 注記必須）。
