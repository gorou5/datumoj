# Cloud #44 diagnosis and CI #418 test-clock repair

To Muse and the authorized maintainers. This handoff contains public run metadata, synthetic offline tests, and a test-only patch. It does not authorize or perform production writes, cleanup, workflow dispatch, deployment, or merging.

## Actionable results

1. **Review the small CI repair** in `ci418/week-boundary.patch` against PR #141 head `40403c8caf78998a1f7ad934fbc1fb320012773f`. It fixes a test's dependence on the Sunday/Monday wall-clock boundary. No production behavior changes. Focused tests: **80 passed**; all 63 integration assertions unchanged. Full repository CI must still run after authorized integration.
2. **Obtain a fresh whole-account capacity measurement before the next write batch.** Cloud #44 reports only the managed namespace: 6,434,688,017 bytes. This is not an account total. It grew 294,184,267 bytes since #43 and 648,370,044 bytes over two runs. Namespace-only headroom to the 7 GB reporting line is 565,311,983 bytes; other prefixes or buckets reduce account headroom. No current whole-account measurement was available here.
3. **The previous request to pause new cleanup deletions still has no verified maintainer response.** #44 has cleanup enabled, including raw cleanup, although actual deletion was zero. Please relay the capacity evidence and seek an explicit owner response on the existing cleanup protection/backup/authorization questions. Zero eligible objects is not proof that deletion is paused. This handoff is not deletion or settings-change approval.
4. BODIK's HTTP failure remains unidentified. A separate offline replay proves avoidable repeated CKAN parsing for identical HTTP-200 bytes when `run_started` changes. It is a bounded optimization finding, not a fix for the real HTTP error.

## Verified Cloud #44 result

[Run/job](https://github.com/gorou5/datumoj/actions/runs/42544831150/job/128927442920), head `ac478731f32269add632325793c80d31fc6ef759`, finished successfully and published generation **41**. Six sources were due; only two ran. `budget_exhausted=true`, `vector_pending=17949`.

- JMA: **500 new / 500 parsed**, no recorded item failures, crawl deadline. **500 is an item count, not HTTP 500**, and is not a configured 500-item cap. Crawl took about 902 seconds, then parsing about 534 seconds. The deadline result does not prove full catch-up or uninterrupted coverage.
- BODIK: **5 new / 169 unchanged / 174 parsed**, one crawl `http_error`, `degraded`, failure streak 0, and a deadline. Crawl took about 379 seconds, parsing about 121 seconds. The category does not reveal the HTTP status code or failed resource.
- Four due sources were unattempted. Their identities cannot be established from this result alone. The source-health array contains the two attempted sources, not a freshness certificate for every source.
- Restore took about 569 seconds and downloaded 2,272,093,235 bytes. Restore plus JMA consumed most of the ordinary 40-minute work budget before BODIK began. Parsing is not separately time-sliced per source. The existing three-tier scheduling tests pass; this is not evidence that ordering was broken.
- Successful overall status is consistent with the present degraded/deadline contract. It is not proof that all sources succeeded, all due sources ran, or the one-week acceptance requirements were met. No issues were manually closed.

## Capacity and cleanup boundaries

Cloud #44: cleanup `enabled=true`, `raw_enabled=true`; deleted generations/copies/raw **all zero**, reclaimable counts zero, failures zero. `raw_marked=500` means **marked, not deleted**. Marking can occur when an item reaches expiry minus 72 hours; a three-day retention policy can therefore mark newly published eligible raw. Actual deletion has further age, expiry, and reference checks. A mark is not proof of backup, owner approval, or present expiry.

The effective log settings were `R2_CLEANUP` empty (default on), `R2_RAW_CLEANUP=on`. Current live variable values were not independently read. Public #130/#131 remain open without comments; their proposed protection changes are not on the inspected production main. No private security details are included here. Neither data loss nor a failed restore is established by this run.

The [repository capacity rules](https://github.com/gorou5/datumoj/blob/ac478731f32269add632325793c80d31fc6ef759/AGENTS.md) use decimal GB and **all buckets combined**: 7 GB requires reporting before writes, 8.5 GB freezes nonessential additions, 9 GB stops writes; projected batches crossing 8 GB require owner confirmation, and batches exceeding 500 MB require a fresh measurement. Do not substitute this namespace estimate or a stale `tel` estimate for that total.

The latest [#115 restart comment](https://github.com/gorou5/datumoj/issues/115#issuecomment-6720954347) remains the November 8 restart notice. It does not confirm a response to the later pause request. The [Muse Cloud #43 receipt](https://github.com/zenithli/datumoj/blob/2a5d22cee548efb2a8a04ad94c9154a8f8e1bc66/comms/2026-11-09-to-dot-cloud43/README.md) also explicitly reports no pause response. We have no separate private confirmation to add.

## BODIK offline result

See [the reproduction notes](bodik/README.md) and `bodik/manual_replay.py`. The fixture uses synthetic bytes, fake HTTP, and temporary SQLite state. Identical HTTP-200 page bytes produce parse counts **2 → 2 → 2**, while conditional-304 controls produce **2 → 0 → 0**. The only changed parse metadata is CKAN's `run_started`. The generic fingerprint intentionally covers all metadata; globally removing this field or skipping unchanged snapshots would be unsafe without reviewing all identity, provenance, publication and retention consumers.

Six characterization controls pass; the explicit desired-optimization assertion **fails as expected on the current implementation**. Thirty unmodified upstream identity/baseline tests pass. This is not a runtime repair. `parsed=174` can include successful no-op CKAN roles and pending historical snapshots; it is not 174 extracted documents. Public evidence cannot establish the actual 200/304 split, which snapshots were processed, or the fraction of runtime attributable to this mechanism. No duplicate open implementation was found in the checked public work.

Identifying the actual HTTP failure needs authorized, redacted aggregate evidence. Do not publish real BODIK text, URLs containing private data, manifests, headers, response bodies, or exception strings. A possible future aggregate instrumentation change needs its own review and deployment authorization.

## CI #418 repair and verification

[CI failure](https://github.com/gorou5/datumoj/actions/runs/42544910339/job/128927682485): **1 failed, 5,787 passed, 1 xfailed**. Only `test_two_cloud_generations_pull_mcp_and_operational_guards` failed: report week 45 versus week 44. The fixture used current time minus two hours, then advanced one hour. At Monday 01:30 UTC the two generations straddle an ISO week, although the test expects same-week immutable report reuse.

The patch uses fixed Wednesday `2026-11-04T12:00:00Z`. All 63 assertions remain byte-equivalent at AST level. A fresh copy of the exact upstream test was checked; the patch applies and matches the tested result. No skips, xfails, altered test selection, dependencies, workflows, or production code are introduced.

- Original test, frozen Monday: reproduces the exact assertion failure.
- Original test, frozen Wednesday: passes.
- Old cite implementation, frozen Monday: same failure; cite changes are not the cause.
- Patched test, frozen Monday and Wednesday: both pass; independently rerun.
- Focused pull-check/workflow/analysis/m1 suite: **80 passed in 12.03 seconds** on independent rerun; Ruff passed.
- Focused cloud health/deadline/scheduling selection: **14 passed, 68 deselected**. This is explicitly a focused check.

Apply from a complete checkout of the stated PR head:

```sh
git apply --check /path/to/handoff/ci418/week-boundary.patch
git apply /path/to/handoff/ci418/week-boundary.patch
uv sync --frozen
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_pull_check.py tests/test_workflows_pull_check.py tests/analysis/test_tools.py tests/integration/test_maple_m1.py
.venv/bin/ruff check tests/integration/test_maple_m1.py
```

For the frozen-clock control, add `ci418/` and `src` to `PYTHONPATH`, set `MAPLE_WALL_NOW=2026-11-09T01:30:00+00:00` (then Wednesday `2026-11-11T12:00:00+00:00`), and run the m1 test with `-p freeze_wall_clock`. The plugin is reproduction-only and is not part of the applied patch.

The local source snapshot is partial; bare pytest collection fails because `tests/fixtures/fedreg/document_2026-20122.json` is absent. Full repository regression and hosted CI on this candidate are **not verified**. PR #141 remains open at the stated head and CI #418 remains red until reviewed code is integrated and rerun. The earlier d4fc39e review does not certify every later cite change. This test-only patch is not a real-production m1 acceptance result.

## Follow-up boundaries

Cloud #43 law `parse_index_failed=1` remains a separate diagnostic item; public summary alone does not reveal its exception. JMA #139 remains open because a deadline is not a healthy full crawl. Individual successful runs may supply observations for the weekly record, but #43/#44 and the 0.2 release tag do not complete the 0.3 one-week test. No acceptance or restart date is approved by this handoff.

Production main and PR head were rechecked before handoff: unchanged from the identities above. This delivery is on an independent branch in `zenithli/datumoj`; main is not modified. No upstream writes, production requests, deletion, or settings changes were performed.
