# BODIK parse-reuse reproduction

Inspected production head: `ac478731f32269add632325793c80d31fc6ef759`. Seven relevant files match exact upstream blobs in `verified-source-blobs.json`. The production runtime is unchanged from `2dd1767572da79a48c47f70aee0e4fc7145170a4`; the intervening main changes are documentation only.

`manual_replay.py` deliberately sits outside the default test suite. It uses synthetic data and fake HTTP only. There is no production patch. Run explicitly from a checkout with locked development dependencies installed:

```sh
PYTHONPATH=src .venv/bin/python -m pytest -q -s /path/to/handoff/bodik/manual_replay.py -k 'not identical_200_new_run_should_reuse_ckan_parse_artifacts'
PYTHONPATH=src .venv/bin/python -m pytest -q /path/to/handoff/bodik/manual_replay.py::test_identical_200_new_run_should_reuse_ckan_parse_artifacts
PYTHONPATH=src .venv/bin/python -m pytest -q /path/to/handoff/bodik/upstream/test_pipeline_identity.py /path/to/handoff/bodik/upstream/test_ckan_baseline.py
.venv/bin/ruff check --config pyproject.toml /path/to/handoff/bodik/manual_replay.py
```

Observed on Python 3.12.14 with locked dependencies:

- First command: **6 passed, 1 deselected**. The exclusion makes the characterization controls explicit, not a claimed all-green suite.
- Second command: **1 failed intentionally**, since current code reparses two identical HTTP-200 pages on a new run. It documents an unsatisfied proposed optimization, not an existing contractual requirement or a completed fix.
- Third command: **30 passed**. The two bundled files are unmodified upstream identity/CKAN-baseline tests.
- Ruff: passed. Full repository suite not verified.

Repeated HTTP-200 replay rows `(new, unchanged, parsed, total parser calls)`:

```text
(2, 0, 2, 2)
(0, 2, 2, 4)
(0, 2, 2, 6)
```

Conditional-304 control:

```text
(2, 0, 2, 2)
(0, 2, 0, 2)
(0, 2, 0, 2)
```

Only crawler bookkeeping `run_started` changes between identical HTTP-200 pages. The generic full-metadata fingerprint therefore changes. Identical metadata reuses correctly; a further parse pass with no new snapshots does not reparse historical records. This is not demonstrated history-wide cache thrashing.

CKAN's wired parser returns empty results for resource/activity/datastore roles, yet successful processing of these no-op roles increments `parsed`. Thus production's `parsed=174` is not a document count. It does not reveal the 200/304 split, exactly which snapshots were processed, or how much of the 121-second parse phase this path consumed.

The aggregate health test also verifies that Cloud #44's one HTTP-category failure with 174 successful crawl outcomes is `degraded`, streak zero, while a deadline leaves the ledger incomplete. Overall success does not establish a complete crawl.

Do not globally strip `run_started` from stored metadata or the generic fingerprint, or skip all unchanged snapshots. Any scoped optimization must preserve incremental crawl completion, context-sensitive parsing, writer identity, citation provenance, publication ordering, no-parser classification, and raw retention. The smallest safe next step is a separate reviewed CKAN design and compatibility tests. Real HTTP diagnosis remains separate and needs authorized redacted aggregate evidence.

Public code references:

- [Full metadata fingerprint](https://github.com/gorou5/datumoj/blob/ac478731f32269add632325793c80d31fc6ef759/src/datumoj/rikolto/core/models.py#L712-L721)
- [CKAN bookkeeping](https://github.com/gorou5/datumoj/blob/ac478731f32269add632325793c80d31fc6ef759/src/datumoj/rikolto/crawlers/ckan.py#L173-L221)
- [Parse reuse](https://github.com/gorou5/datumoj/blob/ac478731f32269add632325793c80d31fc6ef759/src/datumoj/rikolto/pipeline.py#L203-L261)
- [CKAN no-op roles](https://github.com/gorou5/datumoj/blob/ac478731f32269add632325793c80d31fc6ef759/src/datumoj/rikolto/wiring.py#L24-L51)
- [Health contract](https://github.com/gorou5/datumoj/blob/ac478731f32269add632325793c80d31fc6ef759/src/datumoj/rikolto/cloud.py#L253-L280)
- [Historical BODIK issue #93](https://github.com/gorou5/datumoj/issues/93), closed after generation 31; no matching open implementation found during the check.
