# Third-party publication ETL (phase 2)

Loads public third-party publications into the D1 archive table
`third_party_record` (migration `0009_third_party_record.sql`) and, for
the one source that publishes phone numbers, into `third_party_flag`.

## Scripts

| script | input | output | rows |
|---|---|---|---|
| `etl_fsa.py` | `raw/fsa/*.xlsx` — 金融庁 unregistered-operator warning lists (5 lists) | `third_party_record` (source_id=3) | 1,248 |
| `etl_caa.py` | `raw/caa/*.pdf` — 消費者庁 sanctions (62 entries) + §38 cautions (19 entries) | `third_party_record` (source_id=5) | 116 |
| `etl_kokusen.py` | kokusen.go.jp release archive (scanned live, cached to `raw/kokusen/`) — 国民生活センター name-publication releases | `third_party_record` + `third_party_flag` (source_id=5) | per scan |
| `etl_keishicho.py` | `raw/keishicho/*topic304*.csv` — 警視庁メールけいしちょう open data, topic 304 | `third_party_record` (source_id=4), chunked SQL | 54,594 |
| `common.py` | shared: era-date conversion, phone normalization, SQL emit | — | — |

Raw files live outside the repo in
`~/workspace/tel-datumoj/thirdparty/raw/` (phase-1 inventory, with
`INVENTORY.md` and per-source `manifest.csv` as the evidence trail).
Generated SQL is written to an output dir passed as argv[1].

## Rules baked into the parsers

- **No phone numbers are mined from FSA/CAA/Keishicho texts.** Phase-1
  full-text scans verified those sources publish no actor phone numbers;
  the only complete numbers in them are agency/police contact numbers in
  footers, which are not the actor's number. All their records carry
  `has_phone=0`.
- **Kokusen phone numbers come only from the 事業者の情報 block** of a
  name-publication release.
- Every script is idempotent: it deletes its own
  (source_id, source_file_name) scope before inserting.
- `published_on` (publication date) and `captured_at` (download date)
  are kept separate, per the repo evidence standard.

## Running

```sh
python3 etl_fsa.py <outdir>
python3 etl_caa.py <outdir>
python3 etl_kokusen.py <outdir>   # slow: scans ~1.9k detail pages politely
python3 etl_keishicho.py <outdir>
# then, from tel/:
wrangler d1 execute tel_datumoj --remote --file=<outdir>/fsa.sql
wrangler d1 execute tel_datumoj --remote --file=<outdir>/caa.sql
wrangler d1 execute tel_datumoj --remote --file=<outdir>/kokusen_records.sql
wrangler d1 execute tel_datumoj --remote --file=<outdir>/kokusen_flags.sql
for f in <outdir>/keishicho_*.sql; do
  wrangler d1 execute tel_datumoj --remote --file=$f
done
```

D1 free-plan write budget: the Keishicho load is ~55k row writes (its
record indexes are partial and stay empty for that source). Load it
last; if the daily limit interrupts, the remaining chunk files are the
next day's load — chunks are numbered and independently applicable.
