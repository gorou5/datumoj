"""WISTERIA checkpoint m1 (902): pilot demands #002 and V07 as written, from injection to output, no model.

Real recorded bytes (tests/fixtures/{wdi,eurostat,imf_weo,oecd_sdmx}) go through the real parsers, and the four
WEO 2009-2010 archive files go through ``wiring.import_observation_release`` (the code path of
``rikolto observations import-release``) with the real registered ``intl-imf-weo`` source, into one retained
Rikolto generation. Eltiro then runs intake (demand files byte-for-byte from eval/eltiro/demands), profiles,
deterministic matching (``vector=False, model=False``), a simulated human confirmation, fixed-generation
execution, packaging behind the license gate (real registry levels), gap reports and the offline scorer.

What m1 CAN show:
- #002 (three-gdp-reconciliation) and V07 (greece-deficit-revision-chain) are accepted as written; the two other
  pilot files are rejected with ``source_forbidden`` (V13 and #003 are not part of m1).
- Values reach the output equal to the recorded source bytes (hand-computed in the tests), per source and per
  release, with citations that resolve to the recorded snapshot digests.
- The four 2009-2010 WEO releases are readable per release time from the retained generation (-4.509, -6.398,
  -12.866, -13.584 for Greece 2009) next to the API releases.
- The gate uses the real license levels: WDI, OECD and Eurostat are no_outflow, IMF WEO is raw_ok. #002's lineage
  (IMF, OECD, WDI) is refused as a whole and nothing is written; the refusal names a no_outflow source (only the first
  offending row's, see summary F4); an IMF-only run (the m1 gate control) is delivered with only IMF values.

What m1 can NOT show (details and the findings behind each are in results/DATUMOJ-WISTERIA-902/summary.md):
- V07 has no Eurostat binding without a model (B9 is never proposed for the deficit ratio, debt_eur and deficit_eur
  have no candidate), so V07's lineage is IMF only and the real gate lets it out; the plan expects it to be refused.
- #002's local-currency indicator has no OECD candidate, and its USD ``source_spread`` is empty because the recorded WDI
  metadata covers only NY.GDP.MKTP.CN (the local-currency spread is hand-checked for IMF and WDI, Japan only).
- No model and no vector recall (that is 903); every candidate is decided by a simulated person.
- V13 has no recorded table (tests/fixtures/estat_api/README.md) and ILO (304) is on hold; #003 belongs to 904.
- Every gold entry is pending_h2, so no real precision/recall exists.
- Tests whose comment starts with ``Finding:`` assert today's behaviour of Eltiro or a source implementation that
  is not what the demand text asks for; they are listed in the summary together with the task that should fix them.
The only place that does not use the real license levels is ``test_package_with_synthetic_raw_ok_license`` (a
test-only registry copy, SYNTHETIC_RAW_OK, that never describes the real license).
"""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pyarrow.parquet as pq
import pytest
import yaml

from datumoj.eltiro.eval import load_gold, load_thresholds, score_matches
from datumoj.eltiro.executor import run_registered
from datumoj.eltiro.gaps import gap_report
from datumoj.eltiro.intake import IntakeService, load_consumers
from datumoj.eltiro.license_gate import LicenseGateError, check_rows
from datumoj.eltiro.match_store import SQLiteMatchStore
from datumoj.eltiro.matching import match_demand
from datumoj.eltiro.models import OutputManifest
from datumoj.eltiro.package import package_run
from datumoj.eltiro.profile_store import SQLiteProfileStore
from datumoj.eltiro.profiles import generate_profiles
from datumoj.eltiro.registry_store import SQLiteDemandRegistry
from datumoj.rikolto import wiring
from datumoj.rikolto.core.models import Outflow, SnapshotOrder, StoreCopy
from datumoj.rikolto.core.registry import load_registry
from datumoj.rikolto.parsers.eurostat import EurostatParser, read_structure
from datumoj.rikolto.parsers.imf_sdmx import ImfSdmxParser
from datumoj.rikolto.parsers.oecd_sdmx import OecdSdmxParser, read_code_labels, read_valid_from
from datumoj.rikolto.parsers.wdi import WDIParser
from datumoj.rikolto.store.catalog import SqliteCatalogStore
from datumoj.rikolto.store.chunks import SqliteChunkStore
from datumoj.rikolto.store.observations import DuckDBObservationStore, retain_generation
from datumoj.rikolto.testing.fakes import FakeRegistry
from datumoj.rikolto.testing.samples import make_snapshot, make_state_generation, sha
from datumoj.rikolto.wiring import open_generation_views

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests/fixtures"
DEMANDS = ROOT / "eval/eltiro/demands"
GOLD = ROOT / "eval/eltiro/gold"
CONSUMER = "example-analysis-site"
# Sorted: ConsumerModelConfig.authorized_sources must be sorted and unique.
REGISTERED = ("eu-eurostat", "intl-imf-weo", "intl-oecd", "intl-wdi")
# The two sources of the 2026-11-05 offline part; test_wisteria_m2.py still builds on `_existing_part`.
EXISTING = ("eu-eurostat", "intl-wdi")
IMF = "intl-imf-weo"
ARCHIVE = FIX / "imf_weo_archive"
# (file, release_id, released_at, indicator code, table_key, expected 2009 value): release ids and dates are the
# ones the person gave in the archive README ("导入参数"); the values are the README table.
ARCHIVE_RELEASES = (
    ("weo_2009_apr_GRC_excerpt.tsv", "WEO-2009-APR", "2009-04-22", "GGB_NGDP", "WEO:GRC.GGB_NGDP.A", "-4.509"),
    ("weo_2009_oct_GRC_excerpt.tsv", "WEO-2009-OCT", "2009-10-01", "GGB_NGDP", "WEO:GRC.GGB_NGDP.A", "-6.398"),
    ("weo_2010_apr_GRC_excerpt.tsv", "WEO-2010-APR", "2010-04-21", "GGXCNL_NGDP", "WEO:GRC.GGXCNL_NGDP.A", "-12.866"),
    ("weo_2010_oct_GRC_excerpt.tsv", "WEO-2010-OCT", "2010-10-06", "GGXCNL_NGDP", "WEO:GRC.GGXCNL_NGDP.A", "-13.584"),
)
GRC_KEY = "GRC.GGXCNL_NGDP.A"
GRC_CURRENT = "weo_GRC_GGXCNL_NGDP_2000-2015.csv"
CONTROL_ID = "m1-gate-control-imf-only"
# What the simulated person confirms: (indicator key, source, indicator code, fixed dimensions that must match).
GDP_BINDINGS = (
    ("gdp_usd", "intl-wdi", "NY.GDP.MKTP.CD", {}), ("gdp_lcu", "intl-wdi", "NY.GDP.MKTP.CN", {}),
    ("gdp_usd", IMF, "NGDPD", {}), ("gdp_lcu", IMF, "NGDP", {}),
    ("gdp_usd", "intl-oecd", "B1GQ", {"UNIT_MEASURE": "USD_EXC"}),
    ("gdp_lcu", "intl-oecd", "B1GQ", {"UNIT_MEASURE": "XDC"}))
V07_BINDINGS = (
    ("deficit_pct_gdp", IMF, "GGXCNL_NGDP", {}), ("deficit_pct_gdp", IMF, "GGB_NGDP", {}),
    ("deficit_pct_gdp", "eu-eurostat", "B9", {"unit": "PC_GDP"}),
    ("debt_eur", "eu-eurostat", "GD", {"unit": "MIO_EUR"}),
    ("deficit_eur", "eu-eurostat", "B9", {"unit": "MIO_EUR"}))
CONTROL_BINDINGS = (("gdp_usd", IMF, "NGDPD", {}),)


# Evidence goes to a temporary directory unless explicitly requested (never tracked paths).
def _evidence_dir():
    if os.environ.get("DATUMOJ_EVIDENCE_DIR"):
        return Path(os.environ["DATUMOJ_EVIDENCE_DIR"])
    return Path(tempfile.mkdtemp(prefix="datumoj-wisteria-902-"))


def _recording(folder, name):
    entry = json.loads((FIX / folder / "_responses.json").read_bytes())[name]
    entry = entry.get("extracted_from", entry)
    body = (FIX / folder / name).read_bytes()
    return entry, body


def _parsed(specs, *, codelists=False):
    """Real parsers over recorded bytes; WDI pages are joined exactly like MAPLE's evaluation.

    Returns ``(items, wdi_rows)``; each item is ``(source_id, snapshot, ParseResult)`` (test_wisteria_m2.py relies on it,
    and on the default ``codelists=False``: Eurostat read from the recorded DSD without its codelists, so its code
    names stay unknown). m1 passes ``codelists=True``: the recorded DSD with codelists (WISTERIA-315) names the codes.
    """
    out = []
    first, page1 = _recording("wdi", "gdp_cd_cn_JPN-USA_2020-2024_p1.json")
    _, page2 = _recording("wdi", "gdp_cd_cn_JPN-USA_2020-2024_p2.json")
    rows = json.loads(page1, parse_float=Decimal)[1] + json.loads(page2, parse_float=Decimal)[1]
    body = json.dumps([{"page": 1, "pages": 1, "total": len(rows)}, rows], default=str).encode()
    snapshot = make_snapshot("intl-wdi", first["url"], body, fetched_at=datetime.fromisoformat(first["fetched_at"]))
    out.append(("intl-wdi", snapshot, WDIParser().parse(snapshot, body, {
        "request_type": "data", "countries": ["JPN", "USA"], "start_year": 2020, "end_year": 2024,
        "indicators": specs["intl-wdi"].scope["indicators"]})))
    entry, meta_body = _recording("wdi", "indicator_NY.GDP.MKTP.CN.json")
    snapshot = make_snapshot("intl-wdi", entry["url"], meta_body,
                             fetched_at=datetime.fromisoformat(entry["fetched_at"]))
    out.append(("intl-wdi", snapshot, WDIParser().parse(
        snapshot, meta_body, {"request_type": "metadata", "indicator": "NY.GDP.MKTP.CN"})))
    for code, name in (("gov_10dd_edpt1", "gov_10dd_edpt1_EL_2000-2015.xml"),
                       ("nama_10_gdp", "nama_10_gdp_DE-FR-NL_2020-2024.xml")):
        _, structure = _recording("eurostat", f"datastructure_{code.upper()}{'_codelists' if codelists else ''}.xml")
        entry, xml = _recording("eurostat", name)
        snapshot = make_snapshot("eu-eurostat", entry["url"], xml,
                                 fetched_at=datetime.fromisoformat(entry["fetched_at"]))
        out.append(("eu-eurostat", snapshot, EurostatParser().parse(
            snapshot, xml, {"structure": read_structure(structure, code)})))
    # IMF WEO via SDMX-CSV. The Greek key is written oldest release first (as the crawler requests it) so the newest
    # release ends as the table's current one; the recorded fetch times are not in that order, so they are re-spaced.
    base = datetime.fromisoformat(_recording("imf_weo", GRC_CURRENT)[0]["fetched_at"])
    imf = [("weo_2025_oct_GRC_GGXCNL_NGDP_2000-2015.csv", "WEO_2025_OCT_VINTAGE", GRC_KEY, "2000", "2015"),
           ("weo_2026_apr_GRC_GGXCNL_NGDP_2000-2015.csv", "WEO_2026_APR_VINTAGE", GRC_KEY, "2000", "2015"),
           (GRC_CURRENT, "WEO", GRC_KEY, "2000", "2015"),
           ("weo_JPN-USA_NGDPD_2020-2030.csv", "WEO", "JPN+USA.NGDPD.A", "2020", "2030"),
           ("weo_JPN_NGDP_2020-2024.csv", "WEO", "JPN.NGDP.A", "2020", "2024")]
    for index, (name, flow, key, start, end) in enumerate(imf):
        entry, csv_body = _recording("imf_weo", name)
        snapshot = make_snapshot(IMF, entry["url"], csv_body, fetched_at=base + timedelta(seconds=index))
        out.append((IMF, snapshot, ImfSdmxParser().parse(
            snapshot, csv_body, {"dataflow": flow, "key": key, "startPeriod": start, "endPeriod": end})))
    _, constraint = _recording("oecd_sdmx", "contentconstraint_CR_A_DF_TABLE1_EXPENDITURE.xml")
    _, codelists = _recording("oecd_sdmx", "dataflow_DF_TABLE1_EXPENDITURE_codelists.xml")
    flow = "DSD_NAMAIN10@DF_TABLE1_EXPENDITURE"
    entry, csv_body = _recording("oecd_sdmx", "namain10_JPN-USA_2020-2024.csv")
    snapshot = make_snapshot("intl-oecd", entry["url"], csv_body,
                             fetched_at=datetime.fromisoformat(entry["fetched_at"]))
    out.append(("intl-oecd", snapshot, OecdSdmxParser().parse(snapshot, csv_body, {
        "agency": "OECD.SDD.NAD", "id": flow, "version": "2.0",
        "valid_from": read_valid_from(constraint, "OECD.SDD.NAD", flow),
        "code_labels": read_code_labels(codelists)})))
    return out, rows


def _write(root, parsed):
    """Write parsed tables into the live stores (one SnapshotOrder per table write)."""
    store = DuckDBObservationStore(root)
    datasets, seq = {}, 0
    for source_id, snapshot, result in parsed:
        datasets.update({d.dataset_key: d for d in result.datasets})
        tables = {}
        for row in result.observations:
            tables.setdefault(row.table_key, []).append(row)
        for table_key, items in sorted(tables.items()):
            seq += 1
            store.write_table(source_id, table_key, items, SnapshotOrder(fetched_at=snapshot.fetched_at, seq=seq))
    catalog = SqliteCatalogStore(root / "catalog.sqlite")
    catalog.upsert(list(datasets.values()))
    catalog.close()
    SqliteChunkStore(root / "chunks.sqlite").close()


def _retain(root, generation=1):
    """Retain published generation N from the live files (history segments are aliased under parquet/)."""
    copies = []
    for path in [root / "catalog.sqlite", root / "chunks.sqlite", *sorted((root / "parquet").glob("*/*.parquet"))]:
        digest = sha(path.read_bytes())
        copies.append(StoreCopy(name=path.relative_to(root).as_posix(), key=f"copies/sha256/{digest}", sha256=digest,
                                size_bytes=path.stat().st_size,
                                format="sqlite" if path.suffix == ".sqlite" else "parquet"))
    state = make_state_generation(generation).model_copy(update={"stores": copies})
    return retain_generation(root, state.model_dump_json().encode(), root)


def _publish(root, parsed, generation=1):
    """Write parsed tables into the live stores, then retain them as published generation N."""
    _write(root, parsed)
    return _retain(root, generation)


def _existing_part(name):
    """The checkpoint's "parts with data already in the store" (2026-11-05 offline part): only the two sources
    registered then, no model. Kept for test_wisteria_m2.py; the m1 main path injects the demand files as written."""
    data = yaml.safe_load((DEMANDS / name).read_text(encoding="utf-8"))
    for indicator in data["indicators"]:
        indicator["preferred_sources"] = [s for s in indicator.get("preferred_sources", []) if s in EXISTING]
        indicator["min_sources"] = 1
    data["models"] = {"max_tier": "none", "allow_cloud": False}
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode()


def _consumers_yaml(max_tier, allow_cloud, providers):
    return yaml.safe_dump({"consumers": [{
        "id": CONSUMER, "inbox": "consumer/inbox", "outbox": "consumer/outbox",
        "allowed_sources": sorted(REGISTERED), "accept_outflow": ["raw_ok", "derived_only"],
        "models": {"consumer": CONSUMER, "authorized_sources": sorted(REGISTERED), "max_tier": max_tier,
                   "allow_cloud": allow_cloud, "allowed_providers": providers},
        "max_active_demands": 10, "max_calls_per_run": 200}]})


def _control_demand():
    """m1 license-gate CONTROL, not a pilot text: nominal USD GDP bound to IMF WEO (raw_ok) only."""
    pilot = yaml.safe_load((DEMANDS / "three-gdp-reconciliation.yaml").read_text(encoding="utf-8"))
    indicator = {**pilot["indicators"][0], "preferred_sources": [IMF], "min_sources": 1}
    return yaml.safe_dump({
        "schema": "eltiro.request/v0", "id": CONTROL_ID, "version": 1, "consumer": CONSUMER,
        "purpose": "m1 许可闸门对照：只用 IMF（raw_ok）的美元名义 GDP。不是试点原文，不冒充 #002。",
        "source_case": "m1 gate control (not a pilot case)",
        "entities": {"countries": ["JPN", "USA"]},
        "period": {"from": "2020", "to": "2024", "frequency": "annual"}, "revisions": "latest",
        "indicators": [indicator], "align": {"keys": ["country", "year"], "require_same_unit": True},
        "output": {"fields": ["country", "year", "source", "gdp_usd", "release_id", "citation_id"],
                   "format": ["parquet", "csv"]},
        "accept_outflow": ["raw_ok"], "models": {"max_tier": "none", "allow_cloud": False}},
        allow_unicode=True, sort_keys=False)


@pytest.fixture(scope="module")
def pilot(tmp_path_factory):
    def forbidden(*args, **kwargs):
        pytest.fail("checkpoint m1 must never access the network")

    root = tmp_path_factory.mktemp("wisteria-m1").resolve()
    real_registry = load_registry(ROOT / "sources")
    specs = {sid: real_registry.get(sid) for sid in REGISTERED}
    registry = FakeRegistry(list(specs.values()))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
        patch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
        patch.setenv("DATUMOJ_RAW_BACKEND", "local")
        patch.setenv("DATUMOJ_VECTORS", "off")
        parsed, wdi_rows = _parsed(specs, codelists=True)
        _write(root, parsed)
        # The four archive files are imported as history with the real registered source (plan: 3 flows, 3 series).
        deps = wiring.build(root, {"registry": FakeRegistry([specs[IMF]])})
        imported = []
        try:
            for name, release_id, released_at, *_ in ARCHIVE_RELEASES:
                imported.append(wiring.import_observation_release(
                    deps, IMF, ARCHIVE / name, release_id, date.fromisoformat(released_at)))
        finally:
            for part in ("manifest", "chunks", "keyword", "catalog", "change_log"):
                getattr(deps, part).close()
        token = _retain(root)
        (root / "eltiro").mkdir()
        # Model authorization is what #002's original text needs (max_tier medium, allow_cloud true); the matching
        # below still never calls a model, and every registered source has cloud_model_ok: false.
        (root / "eltiro" / "consumers.yaml").write_text(
            _consumers_yaml("medium", True, ["none", "claude_plan"]), encoding="utf-8")
        inbox = root / "consumer" / "inbox"
        inbox.mkdir(parents=True)
        # The four pilot demand files exactly as written, plus the m1 gate control.
        for path in sorted(DEMANDS.glob("*.yaml")):
            (inbox / path.name).write_bytes(path.read_bytes())
        (inbox / f"{CONTROL_ID}.yaml").write_text(_control_demand(), encoding="utf-8")
        with SQLiteDemandRegistry(root / "eltiro" / "demands.sqlite3") as demands:
            intake = IntakeService(root, demands, registry).scan()
            receipts = {r["demand_id"]: r["reason_code"] for r in (
                json.loads(p.read_text()) for p in (root / "consumer" / "outbox").glob("*.json"))}
            consumer = IntakeService(root, demands, registry).consumer(CONSUMER)
            with (open_generation_views(root, token) as views, SQLiteProfileStore(root) as profiles,
                  SQLiteMatchStore(root) as matches):
                assert views.generation_token == token
                profile_list = generate_profiles(1, observations=views.observations, catalog=views.catalog,
                                                 chunks=views.chunks, registry=registry, store=profiles)
                results = {demand.id: match_demand(
                    demand, 1, profiles=profiles, consumer=consumer, observations=views.observations,
                    catalog=views.catalog, chunks=views.chunks, store=matches, vector=False, model=False)
                    for demand in demands.list_active()}
        yield {"root": root, "token": token, "registry": registry, "real_registry": real_registry, "specs": specs,
               "wdi_rows": wdi_rows, "wdi_sha": parsed[0][1].sha256, "intake": intake, "receipts": receipts,
               "imported": imported, "profiles": profile_list, "results": results}


def _wanted(record, bindings):
    series = record.series
    return any(
        (record.indicator_key, series.source_id, series.indicator_code) == (key, source, code)
        and all(series.fixed_dimensions.get(dim) == value for dim, value in dims.items())
        for key, source, code, dims in bindings)


def _confirm_and_run(pilot, demand_id, bindings):
    """Simulated human decision (as `eltiro matches confirm/reject` records it), then a fixed-generation run."""
    root = pilot["root"]
    with SQLiteDemandRegistry(root / "eltiro" / "demands.sqlite3") as demands, SQLiteMatchStore(root) as matches:
        demand = next(d for d in demands.list_active() if d.id == demand_id)
        for record in matches.for_demand(demand.id, demand.version, 1):
            matches.set_confirmation(demand.id, demand.version, record.indicator_key, record.series, 1,
                                     "confirmed" if _wanted(record, bindings) else "rejected")
        record = run_registered(demand.id, generation_token=pilot["token"], data_root=root,
                                registry=pilot["registry"], demands=demands)
    run = root / "eltiro" / "runs" / record["run_id"]
    rows = pq.read_table(run / "data.parquet").to_pylist()
    citations = json.loads((run / "citations.json").read_bytes())
    gaps = json.loads((run / "gaps.json").read_bytes())
    return record, rows, citations, gaps


@pytest.fixture(scope="module")
def unconfirmed_runs(pilot):
    """The first `eltiro run` of each pilot, before any decision (the runs below depend on this, so it comes first)."""
    root = pilot["root"]
    out = {}
    with SQLiteDemandRegistry(root / "eltiro" / "demands.sqlite3") as demands:
        for demand_id in ("three-gdp-reconciliation", "greece-deficit-revision-chain"):
            record = run_registered(demand_id, generation_token=pilot["token"], data_root=root,
                                    registry=pilot["registry"], demands=demands)
            run = root / "eltiro" / "runs" / record["run_id"]
            out[demand_id] = (record, json.loads((run / "gaps.json").read_bytes()))
    return out


@pytest.fixture(scope="module")
def gdp_run(pilot, unconfirmed_runs):
    return _confirm_and_run(pilot, "three-gdp-reconciliation", GDP_BINDINGS)


@pytest.fixture(scope="module")
def v07_run(pilot, unconfirmed_runs):
    return _confirm_and_run(pilot, "greece-deficit-revision-chain", V07_BINDINGS)


@pytest.fixture(scope="module")
def control_run(pilot, unconfirmed_runs):
    return _confirm_and_run(pilot, CONTROL_ID, CONTROL_BINDINGS)


def _imf_values(name, code, countries):
    """Recorded IMF OBS_VALUE per (country, year) 2020-2024, read straight from the CSV bytes (not the parser)."""
    text = (FIX / "imf_weo" / name).read_text(encoding="utf-8-sig")
    return {(row["COUNTRY"], int(row["TIME_PERIOD"])): Decimal(row["OBS_VALUE"])
            for row in csv.DictReader(io.StringIO(text, newline=""))
            if row["INDICATOR"] == code and row["COUNTRY"] in countries and "2020" <= row["TIME_PERIOD"] <= "2024"}


def _oecd_values(unit):
    """Recorded OECD OBS_VALUE per (country, year) for one UNIT_MEASURE, read straight from the CSV bytes."""
    text = (FIX / "oecd_sdmx" / "namain10_JPN-USA_2020-2024.csv").read_text(encoding="utf-8")
    return {(row["REF_AREA"], int(row["TIME_PERIOD"])): Decimal(row["OBS_VALUE"])
            for row in csv.DictReader(io.StringIO(text, newline="")) if row["UNIT_MEASURE"] == unit}


def _by_key(rows, symbols):
    return {(r["symbol"], r["source"], r["country"], r["period"].year): r["value"] for r in rows
            if r["symbol"] in symbols}


# ---------------------------------------------------------------- injection and registration


def test_real_license_levels_of_the_four_sources(pilot):
    levels = {sid: (spec.license.outflow, spec.license.cloud_model_ok) for sid, spec in pilot["specs"].items()}
    assert levels == {"eu-eurostat": (Outflow.no_outflow, False), "intl-imf-weo": (Outflow.raw_ok, False),
                      "intl-oecd": (Outflow.no_outflow, False), "intl-wdi": (Outflow.no_outflow, False)}


def test_pilot_demands_as_written_are_accepted_or_rejected_for_the_right_source(pilot):
    # #002 and V07 are accepted without any rewriting; the control is the m1 gate control (not a pilot text).
    # V13 (japan-monthly-earnings-revisions) prefers jp-estat-files (registered, but this consumer is not
    # authorized for it; its table 4 is not recorded) and intl-ilo (not registered: 304 is on hold).
    # #003 (trade-mirror) prefers intl-un-comtrade (the registered id is intl-comtrade) and intl-wto (registered, not
    # authorized here; WTO has no bilateral goods trade, 311 was cancelled).
    assert pilot["intake"] == {"accepted": 3, "rejected": 2}
    assert pilot["receipts"] == {
        "three-gdp-reconciliation": "accepted", "greece-deficit-revision-chain": "accepted",
        CONTROL_ID: "accepted",
        "japan-monthly-earnings-revisions": "source_forbidden", "trade-mirror": "source_forbidden"}


def test_original_three_gdp_text_needs_model_authorization_not_model_use(tmp_path):
    # The same consumer with max_tier "low" and no cloud rejects #002 as written (it asks for medium + cloud);
    # V07 (low, no cloud) still passes. This is why the host configuration above grants medium + cloud: the
    # authorization is a ceiling on what a demand may ask for, never a call.
    root = tmp_path / "data"
    (root / "eltiro").mkdir(parents=True)
    (root / "eltiro" / "consumers.yaml").write_text(_consumers_yaml("low", False, ["none", "ollama"]), encoding="utf-8")
    inbox = root / "consumer" / "inbox"
    inbox.mkdir(parents=True)
    for name in ("three-gdp-reconciliation.yaml", "greece-deficit-revision-chain.yaml"):
        (inbox / name).write_bytes((DEMANDS / name).read_bytes())
    registry = FakeRegistry([load_registry(ROOT / "sources").get(sid) for sid in REGISTERED])
    with SQLiteDemandRegistry(root / "eltiro" / "demands.sqlite3") as demands:
        assert IntakeService(root, demands, registry).scan() == {"accepted": 1, "rejected": 1}
    receipts = {r["demand_id"]: r["reason_code"] for r in (
        json.loads(p.read_text()) for p in (root / "consumer" / "outbox").glob("*.json"))}
    assert receipts == {"three-gdp-reconciliation": "model_forbidden", "greece-deficit-revision-chain": "accepted"}


def test_archive_files_are_imported_as_history_releases(pilot):
    assert [(r["release_id"], r["released_at"]) for r in pilot["imported"]] == [
        (release, released) for _, release, released, *_ in ARCHIVE_RELEASES]
    for report, (name, *_) in zip(pilot["imported"], ARCHIVE_RELEASES, strict=True):
        assert report["snapshot_sha256"] == sha((ARCHIVE / name).read_bytes())


# ---------------------------------------------------------------- profiles and matching


def test_profiles_cover_the_published_generation(pilot):
    by_source = {}
    for p in pilot["profiles"]:
        by_source.setdefault(p.series.source_id, []).append(p)
    assert set(by_source) == set(REGISTERED)
    wdi = {p.series.indicator_code: p for p in by_source["intl-wdi"]}
    assert set(wdi) == {"NY.GDP.MKTP.CD", "NY.GDP.MKTP.CN"}
    assert all(p.availability == "observations" and p.frequency == "annual" for p in wdi.values())
    assert wdi["NY.GDP.MKTP.CD"].areas == ["JPN", "USA"]
    # 317: scales come only from what a parser declares with evidence. The recorded WDI indicator metadata is of
    # CN only, so CD stays unconfirmed (no metadata sample was recorded for it).
    assert wdi["NY.GDP.MKTP.CN"].scale.multiplier == 1.0 and "NY.GDP.MKTP.CN" in wdi["NY.GDP.MKTP.CN"].scale.evidence
    assert wdi["NY.GDP.MKTP.CD"].scale.multiplier is None and wdi["NY.GDP.MKTP.CD"].scale.evidence is None
    # IMF: the country is part of the series identity (COUNTRY is a fixed dimension). The archive-only 2009 series
    # GGB_NGDP is a series of its own; the 2010 archive releases share the API's GGXCNL_NGDP series (316).
    imf = {(p.series.indicator_code, p.series.fixed_dimensions["COUNTRY"]): p for p in by_source[IMF]}
    assert set(imf) == {("NGDPD", "JPN"), ("NGDPD", "USA"), ("NGDP", "JPN"), ("GGXCNL_NGDP", "GRC"), ("GGB_NGDP", "GRC")}
    assert {key: p.scale.multiplier for key, p in imf.items() if key[0] != "GGB_NGDP"} == {
        ("NGDPD", "JPN"): 1.0, ("NGDPD", "USA"): 1.0, ("NGDP", "JPN"): 1.0, ("GGXCNL_NGDP", "GRC"): 1.0}
    assert {p.unit for p in by_source[IMF]} == {"USD", "XDC", "PT"}
    assert imf["GGXCNL_NGDP", "GRC"].names == {
        "und": "Net lending (+) / net borrowing (-), General government, Percent of GDP"}
    # Finding (WISTERIA-313/317 follow-up): `import_observation_release` writes observations only, never the archive
    # dataset, so the archive-only series GGB_NGDP has no scale declaration (317 puts it on the archive Dataset, which
    # no one stores) and no dataset title/attribution. The values are unaffected (the parser already multiplied by the
    # file's Scale column); percent series need no scale in V07. Fix: the import path should upsert the parsed datasets.
    assert imf["GGB_NGDP", "GRC"].scale.multiplier is None
    # OECD: nominal GDP (B1GQ) JPN/USA x {XDC, USD_EXC}; the codelist name reaches the profile (315) and the unit text
    # keeps UNIT_MULT (x10^6) apart from the value, which 317 turns into a confirmed scale.
    oecd = {(p.series.fixed_dimensions["REF_AREA"], p.series.fixed_dimensions["UNIT_MEASURE"]): p
            for p in by_source["intl-oecd"]}
    assert {key: p.unit for key, p in oecd.items()} == {
        ("JPN", "XDC"): "JPY ×10^6", ("JPN", "USD_EXC"): "USD_EXC ×10^6",
        ("USA", "XDC"): "USD ×10^6", ("USA", "USD_EXC"): "USD_EXC ×10^6"}
    assert all(p.names == {"en": "Gross domestic product", "und": "B1GQ"} and p.scale.multiplier == 1e6
               for p in oecd.values())
    eurostat = {(p.series.table_key, p.series.indicator_code, p.series.fixed_dimensions["unit"]): p
                for p in by_source["eu-eurostat"] if p.series.table_key == "gov_10dd_edpt1"}
    assert {key: p.names["en"] for key, p in eurostat.items()} == {
        ("gov_10dd_edpt1", "B9", "MIO_EUR"): "Net lending (+)/net borrowing (-)",
        ("gov_10dd_edpt1", "B9", "PC_GDP"): "Net lending (+)/net borrowing (-)",
        ("gov_10dd_edpt1", "GD", "MIO_EUR"): "Government consolidated gross debt",
        ("gov_10dd_edpt1", "GD", "PC_GDP"): "Government consolidated gross debt"}
    # Eurostat: CP_MEUR only -> 10^6; PC_GDP + MIO_EUR mixed in one table -> unconfirmed.
    nama = [p for p in by_source["eu-eurostat"] if p.series.table_key == "nama_10_gdp"]
    assert len(nama) == 3 and all(p.scale.multiplier == 1e6 and "CP_MEUR" in p.scale.evidence for p in nama)
    assert all(p.scale.multiplier is None for p in eurostat.values())


def test_oecd_values_are_in_the_generation_exactly_as_recorded(pilot):
    expected = {(a, u, str(y)): v for u in ("USD_EXC", "XDC") for (a, y), v in _oecd_values(u).items()}
    assert len(expected) == 20
    with open_generation_views(pilot["root"], pilot["token"]) as views:
        result = views.observations.query_readonly(
            "SELECT area_code, json_extract_string(dims, '/UNIT_MEASURE'), time_label, value FROM observations "
            "WHERE source_id = 'intl-oecd'")
    assert {(a, u, t): v for a, u, t, v in result.rows} == expected
    # UNIT_MULT=6 travels in the unit text: 5189197.544 x 10^6 is the OECD JPN 2020 value in USD.
    assert expected["JPN", "USD_EXC", "2020"] * 10**6 == Decimal(5189197544000)


def test_deterministic_matching_queues_candidates_and_never_auto_binds(pilot):
    gdp = pilot["results"]["three-gdp-reconciliation"]
    # vector=False and model=False: only the deterministic tier runs.
    assert {r.tier for r in gdp.records} == {"deterministic"}
    # Unit text differs ("current US$" vs "USD" vs "USD_EXC ×10^6"), so nothing auto-binds: a person decides.
    assert not any(r.auto_bind for r in gdp.records)
    assert len(gdp.queue) == len(gdp.records)
    # Candidates that pass the keyword filter but lack evidence would be listed here; none are.
    assert not gdp.missing_evidence
    # The right series of every source are among the candidates (next to near misses a person rejects), OECD included
    # (315: its codelist name "Gross domestic product" now matches the gdp_usd examples).
    proposed = {(r.indicator_key, r.series.source_id, r.series.indicator_code, r.series.fixed_dimensions.get("UNIT_MEASURE"))
                for r in gdp.records}
    assert {(key, source, code, unit) for key, source, code, dims in GDP_BINDINGS
            for unit in [dims.get("UNIT_MEASURE")] if (key, source) != ("gdp_lcu", "intl-oecd")} <= proposed
    # Nominal-GDP USD candidates of IMF carry both countries of the demand's scope.
    assert {r.series.fixed_dimensions["COUNTRY"] for r in gdp.records
            if r.series.source_id == IMF and r.series.indicator_code == "NGDPD"} == {"JPN", "USA"}


def test_oecd_series_is_not_proposed_for_gdp_lcu(pilot):
    # Finding (matching, 903 / demand wording): the keyword tier needs a shared word between the indicator text
    # (concept + positive examples) and the profile text. gdp_usd lists "Gross domestic product, current prices" as an
    # example, so OECD's codelist name "Gross domestic product" matches; gdp_lcu's only example says "GDP (current LCU)"
    # and the OECD profile never spells "GDP", so no OECD candidate exists for gdp_lcu. A person can only confirm an
    # existing candidate, so #002's local-currency indicator cannot get its OECD series without the vector/model tier.
    gdp = pilot["results"]["three-gdp-reconciliation"]
    assert {r.indicator_key for r in gdp.records if r.series.source_id == "intl-oecd"} == {"gdp_usd"}
    assert {r.indicator_key for r in gdp.records if r.series.source_id == IMF and r.series.indicator_code == "NGDP"} == {
        "gdp_usd", "gdp_lcu"}


@pytest.mark.xfail(strict=True, reason=(
    "Known matching-quality gap: keyword overlap ranks GDP (current LCU) above GDP (current US$) for the "
    "gdp_usd indicator; the unit text differs ('current US$' vs 'USD') and is not used for ranking."))
def test_us_dollar_series_ranks_first_for_gdp_usd(pilot):
    gdp = pilot["results"]["three-gdp-reconciliation"]
    usd = sorted((r for r in gdp.records if r.indicator_key == "gdp_usd"), key=lambda r: -r.confidence)
    assert usd[0].series.indicator_code == "NY.GDP.MKTP.CD"


def test_matching_findings_for_the_greek_indicators_are_visible(pilot):
    """Findings for 903, asserted so a change in behaviour is noticed, not hidden."""
    greece = pilot["results"]["greece-deficit-revision-chain"]
    by_indicator = {}
    for r in greece.records:
        by_indicator.setdefault(r.indicator_key, []).append(r)
    assert not any(r.auto_bind for r in greece.records)
    # IMF: the right series (GGXCNL_NGDP, and the archive-only GGB_NGDP) are proposed, ranked first and second among
    # IMF candidates; unrelated GDP series come along and a person rejects them.
    imf = sorted((r for r in by_indicator["deficit_pct_gdp"] if r.series.source_id == IMF), key=lambda r: -r.confidence)
    assert imf[0].series.indicator_code == "GGXCNL_NGDP"
    assert {r.series.indicator_code for r in imf} >= {"GGXCNL_NGDP", "GGB_NGDP"}
    # Finding (matching, 903 / demand wording): V07's concept texts are Chinese and only deficit_pct_gdp has an English
    # example ("Government deficit/surplus ... (gov_10dd_edpt1)"). The Eurostat codelist name of B9 ("Net lending
    # (+)/net borrowing (-)") shares no word with it, so B9 is not proposed for deficit_pct_gdp (GD, "Government
    # consolidated gross debt", is, through the word "government"), and debt_eur / deficit_eur have no candidate at all.
    # `eltiro matches confirm` only decides existing candidates, so V07 gets no correct Eurostat binding without 903.
    eurostat = {(r.series.indicator_code, r.series.fixed_dimensions["unit"]) for r in by_indicator["deficit_pct_gdp"]
                if r.series.source_id == "eu-eurostat"}
    assert eurostat == {("GD", "MIO_EUR"), ("GD", "PC_GDP")}
    assert set(by_indicator) == {"deficit_pct_gdp"}


def test_eurostat_series_identity_does_not_split_by_period(pilot):
    # TIME_PERIOD restates each row's own period, so it is not part of the series identity.
    deficit = [p for p in pilot["profiles"] if p.series.source_id == "eu-eurostat"
               and p.series.table_key == "gov_10dd_edpt1" and p.series.indicator_code == "B9"
               and p.series.fixed_dimensions.get("unit") == "PC_GDP"]
    assert len(deficit) == 1
    profile = deficit[0]
    assert "TIME_PERIOD" not in profile.series.fixed_dimensions
    assert profile.series.fixed_dimensions == {"freq": "A", "geo": "EL", "na_item": "B9", "sector": "S13",
                                               "unit": "PC_GDP"}
    assert (profile.time_start.year, profile.time_end.year) == (2000, 2015)
    # Four gov_10dd_edpt1 series (B9/GD x PC_GDP/MIO_EUR) and three GDP series (DE/FR/NL).
    eurostat = [p for p in pilot["profiles"] if p.series.source_id == "eu-eurostat"]
    assert len(eurostat) == 7


def test_first_run_before_any_decision_is_empty_and_names_the_pending_candidates(pilot, unconfirmed_runs):
    # There is no separate profile/match command: the first `eltiro run` builds matches (here they already exist) and,
    # with nothing confirmed and no auto-binding, writes an empty output whose gaps say what waits for a person.
    for demand_id, (record, gaps) in unconfirmed_runs.items():
        assert record["row_count"] == 0 and record["generation_token"] == pilot["token"]
        symbol = "deficit_pct_gdp" if "greece" in demand_id else "gdp_usd"
        assert {(g["symbol"], g["reason"]) for g in gaps} >= {(symbol, "pending_confirmation"),
                                                              (symbol, "unbound_indicator")}


# ---------------------------------------------------------------- #002: three GDPs for one country


def test_gdp_run_values_equal_the_recorded_bytes_per_source(pilot, gdp_run):
    record, rows, _, _ = gdp_run
    assert record["generation"] == 1 and record["generation_token"] == pilot["token"]
    expected = {}
    for symbol, code in (("gdp_usd", "NY.GDP.MKTP.CD"), ("gdp_lcu", "NY.GDP.MKTP.CN")):
        for item in pilot["wdi_rows"]:
            if item["indicator"]["id"] == code and item["value"] is not None:
                expected[(symbol, "intl-wdi", item["countryiso3code"], int(item["date"]))] = Decimal(str(item["value"]))
    usd = _imf_values("weo_JPN-USA_NGDPD_2020-2030.csv", "NGDPD", {"JPN", "USA"})
    lcu = _imf_values("weo_JPN_NGDP_2020-2024.csv", "NGDP", {"JPN"})
    expected.update({("gdp_usd", IMF, c, y): v for (c, y), v in usd.items()})
    expected.update({("gdp_lcu", IMF, c, y): v for (c, y), v in lcu.items()})
    expected.update({("gdp_usd", "intl-oecd", c, y): v for (c, y), v in _oecd_values("USD_EXC").items()})
    assert len(usd) == 10 and len(lcu) == 5 and len(expected) == 20 + 10 + 5 + 10
    raw = [r for r in rows if r["symbol"] in ("gdp_usd", "gdp_lcu")]
    # One row per (symbol, source, country, year): the sources are never merged, and every value equals the
    # recorded source byte-for-byte as Decimal (source original, not a consensus).
    assert _by_key(raw, ("gdp_usd", "gdp_lcu")) == expected and len(raw) == len(expected)
    # The IMF value is the full USD value (SCALE=9 is only a display magnitude); OECD publishes millions (UNIT_MULT=6).
    assert expected["gdp_usd", IMF, "JPN", 2020] == Decimal(5189196607000)
    assert expected["gdp_usd", "intl-wdi", "JPN", 2020] == Decimal("5189197543645.38")
    assert expected["gdp_usd", "intl-oecd", "JPN", 2020] == Decimal("5189197.544")
    assert {r["source"] for r in raw} == {IMF, "intl-wdi", "intl-oecd"}


def test_gdp_rows_resolve_to_recorded_snapshots(pilot, gdp_run):
    _, rows, citations, _ = gdp_run
    oecd = sha((FIX / "oecd_sdmx" / "namain10_JPN-USA_2020-2024.csv").read_bytes())
    digest = {("gdp_usd", IMF): sha((FIX / "imf_weo" / "weo_JPN-USA_NGDPD_2020-2030.csv").read_bytes()),
              ("gdp_lcu", IMF): sha((FIX / "imf_weo" / "weo_JPN_NGDP_2020-2024.csv").read_bytes()),
              ("gdp_usd", "intl-oecd"): oecd,
              # WDI: the two recorded pages joined into one response (see _parsed).
              ("gdp_usd", "intl-wdi"): pilot["wdi_sha"], ("gdp_lcu", "intl-wdi"): pilot["wdi_sha"]}
    by_id = {c["citation_id"]: c["citation"] for c in citations}
    checked = 0
    for row in rows:
        if row["symbol"] not in ("gdp_usd", "gdp_lcu"):
            continue
        inputs = by_id[row["citation_id"]]["inputs"]
        assert [(i["source_id"], i["snapshot_sha256"]) for i in inputs] == [
            (row["source"], digest[row["symbol"], row["source"]])]
        assert all(i["table_key"] and i["obs_id"] for i in inputs)
        checked += 1
    assert checked == 45
    # The IMF and OECD rows name their release; the WDI rows (no release concept) fall back to the snapshot digest.
    assert {r["release_id"] for r in rows if r["symbol"].startswith("gdp_") and r["source"] == IMF} == {"WEO-2026-OCT"}
    assert {r["release_id"] for r in rows if r["symbol"] == "gdp_usd" and r["source"] == "intl-oecd"} == {
        "DSD_NAMAIN10@DF_TABLE1_EXPENDITURE@2026-11-16T07:54:30Z"}
    assert {r["release_id"] for r in rows if r["symbol"].startswith("gdp_") and r["source"] == "intl-wdi"} == {
        pilot["wdi_sha"]}


def test_gdp_gaps_list_what_the_samples_lack(gdp_run):
    _, rows, _, gaps = gdp_run
    reasons = {(g["symbol"], g["reason"]) for g in gaps}
    assert reasons == {
        # DEU, FRA, GBR are not in the recordings of any source.
        ("gdp_usd", "entity_unavailable"), ("gdp_lcu", "entity_unavailable"),
        # gdp_lcu wants 2 sources: IMF has no recorded USA local-currency series and OECD has no candidate (finding above).
        ("gdp_lcu", "insufficient_sources"),
        # The recorded WDI indicator metadata is of NY.GDP.MKTP.CN only, so CD's scale is unconfirmed and cannot be
        # rescaled (a missing recording, not a defect: the real WDI crawl fetches the metadata of every indicator).
        ("gdp_usd_units", "unconfirmed_scale"),
        # ...which leaves WDI's cell empty, so the USD spread has a missing input everywhere;
        ("spread_usd", "missing_input"),
        # and the USA local-currency spread has a single source.
        ("spread_lcu", "insufficient_sources")}
    assert {g["entity"] for g in gaps if g["reason"] == "entity_unavailable"} == {"DEU", "FRA", "GBR"}
    # Gaps are reported, never filled: the cells are empty, not zero.
    units = {(r["source"], r["country"], r["period"].year): r["value"] for r in rows if r["symbol"] == "gdp_usd_units"}
    assert all(units[source, country, year] is None for source, country, year in units if source == "intl-wdi")
    # IMF (scale 1) and OECD (UNIT_MULT=6, confirmed) are rescaled to one USD: 5189197.544 x 10^6 = 5189197544000.
    assert units[IMF, "JPN", 2020] == Decimal(5189196607000) and units["intl-oecd", "JPN", 2020] == Decimal(5189197544000)
    assert all(r["value"] is None and r["spread_range"] is None for r in rows if r["symbol"] == "spread_usd")
    assert all(r["value"] is None for r in rows if r["symbol"] == "spread_lcu" and r["country"] == "USA")
    # Finding (needs a WDI metadata recording for NY.GDP.MKTP.CD, not an Eltiro change): once that scale is confirmed the
    # USD spread over IMF (5189196607000), OECD (5189197.544 x 10^6) and WDI (5189197543645.38) for JPN 2020 would be
    # median 5189197543645.38, range 5189197544000 - 5189196607000 = 937000; today none of it is output.


def test_source_spread_of_local_currency_matches_the_hand_computed_values(pilot, gdp_run):
    _, rows, _, _ = gdp_run
    imf = _imf_values("weo_JPN_NGDP_2020-2024.csv", "NGDP", {"JPN"})
    wdi = {int(i["date"]): Decimal(str(i["value"])) for i in pilot["wdi_rows"]
           if i["indicator"]["id"] == "NY.GDP.MKTP.CN" and i["countryiso3code"] == "JPN" and i["value"] is not None}
    for year in range(2020, 2025):
        a, b = imf["JPN", year], wdi[year]
        median = (a + b) / 2  # two sources: the median is their mean
        spread = {r["source"]: r for r in rows if r["symbol"] == "spread_lcu" and r["country"] == "JPN"
                  and r["period"].year == year}
        assert set(spread) == {IMF, "intl-wdi"}
        assert spread[IMF]["value"] == a - median and spread["intl-wdi"]["value"] == b - median
        assert spread[IMF]["spread_range"] == spread["intl-wdi"]["spread_range"] == abs(a - b)
    # JPN 2020: IMF 554074300000000 and WDI 554074400000000 (both full local-currency values).
    assert (imf["JPN", 2020], wdi[2020]) == (Decimal(554074300000000), Decimal(554074400000000))
    first = {r["source"]: r["value"] for r in rows if r["symbol"] == "spread_lcu" and r["country"] == "JPN"
             and r["period"].year == 2020}
    assert first == {IMF: Decimal(-50000000), "intl-wdi": Decimal(50000000)}


# ---------------------------------------------------------------- V07: Greek deficit revision chain


def test_four_historical_releases_and_the_current_ones_are_readable_by_release_time(pilot):
    greece = ("SELECT release_id, released_at, value, note, snapshot_sha256 FROM observations "
              "WHERE source_id = 'intl-imf-weo' AND table_key = '{table}' AND area_code = 'GRC' "
              "AND indicator_code = '{code}' AND time_label = '2009' AND unit = 'PT'")
    with open_generation_views(pilot["root"], pilot["token"]) as views:
        for name, release, released_at, code, table, value in ARCHIVE_RELEASES:
            result = views.observations.query_readonly(greece.format(table=table, code=code), as_of_release=release)
            (got_release, got_date, got, note, digest), = result.rows
            assert (got_release, got_date, got) == (release, date.fromisoformat(released_at), Decimal(value))
            assert digest == sha((ARCHIVE / name).read_bytes())  # the snapshot of the archive file as downloaded
            # GGB_NGDP (2009 files) is the same concept as GGXCNL_NGDP: the correspondence is written on the row.
            assert ("代码对应" in note) == (code == "GGB_NGDP")
        # The API releases of the same series as the 2010 archive files (316): 2009 = -15.424 / -15.423608.
        current = greece.format(table="WEO:GRC.GGXCNL_NGDP.A", code="GGXCNL_NGDP")
        for name, release, released_at, value in (
                ("weo_2025_oct_GRC_GGXCNL_NGDP_2000-2015.csv", "WEO-2025-OCT", "2025-10-14", "-15.424"),
                ("weo_2026_apr_GRC_GGXCNL_NGDP_2000-2015.csv", "WEO-2026-APR", "2026-04-14", "-15.423608"),
                (GRC_CURRENT, "WEO-2026-OCT", "2026-10-13", "-15.423608")):
            (got_release, got_date, got, _, digest), = views.observations.query_readonly(
                current, as_of_release=release).rows
            assert (got_release, got_date, got) == (release, date.fromisoformat(released_at), Decimal(value))
            assert digest == sha((FIX / "imf_weo" / name).read_bytes())
        releases = {table: [(r.release_key, r.released_at.isoformat()) for r in views.observations.list_releases(
            source_id=IMF, table_key=table)] for table in ("WEO:GRC.GGB_NGDP.A", "WEO:GRC.GGXCNL_NGDP.A")}
    assert releases == {
        "WEO:GRC.GGB_NGDP.A": [("WEO-2009-APR", "2009-04-22"), ("WEO-2009-OCT", "2009-10-01")],
        "WEO:GRC.GGXCNL_NGDP.A": [("WEO-2010-APR", "2010-04-21"), ("WEO-2010-OCT", "2010-10-06"),
                                  ("WEO-2025-OCT", "2025-10-14"), ("WEO-2026-APR", "2026-04-14"),
                                  ("WEO-2026-OCT", "2026-10-13")]}


def _deficit_2009(rows):
    return {(r["release_id"], r["released_at"].isoformat()): (r["value"], r["note"]) for r in rows
            if r["symbol"] == "deficit_pct_gdp" and r["country"] == "GRC" and r["period"].year == 2009}


def test_greek_run_orders_releases_by_release_time_and_marks_the_code_correspondence(v07_run):
    _, rows, citations, _ = v07_run
    deficit = _deficit_2009(rows)
    # All seven IMF releases of Greece 2009: the four archive releases (2009-04 -4.509, 2009-10 -6.398, 2010-04 -12.866,
    # 2010-10 -13.584) and the three API releases, each with its own release_id and release date.
    assert {key: value for key, (value, _) in deficit.items()} == {
        ("WEO-2009-APR", "2009-04-22"): Decimal("-4.509"), ("WEO-2009-OCT", "2009-10-01"): Decimal("-6.398"),
        ("WEO-2010-APR", "2010-04-21"): Decimal("-12.866"), ("WEO-2010-OCT", "2010-10-06"): Decimal("-13.584"),
        ("WEO-2025-OCT", "2025-10-14"): Decimal("-15.424"), ("WEO-2026-APR", "2026-04-14"): Decimal("-15.423608"),
        ("WEO-2026-OCT", "2026-10-13"): Decimal("-15.423608")}
    assert all(("代码对应" in note) == release.startswith("WEO-2009") for (release, _), (_, note) in deficit.items())
    # release_position follows the release date within each series (the revision chain is ordered by release time).
    for table in ("WEO:GRC.GGB_NGDP.A", "WEO:GRC.GGXCNL_NGDP.A"):
        chain = sorted((r["release_position"], r["released_at"]) for r in rows
                       if r["symbol"] == "deficit_pct_gdp" and table in r["series"])
        assert [d for _, d in chain] == sorted(d for _, d in chain)
    # revision_delta is relative to the series' first release. The code change splits the chain in two series (the
    # person decided to keep the original code, 2026-11-20), so the baseline restarts at 2010-04:
    # GGB_NGDP: -6.398 - (-4.509) = -1.889; GGXCNL_NGDP: -13.584 - (-12.866) = -0.718, -15.424 - (-12.866) = -2.558, ...
    # Finding (Eltiro, design): an indicator cannot bind two series as one revision chain, so revision_delta does not
    # run from 2009-04 across the code change. The correspondence is only written on the rows (note), not used.
    delta = {r["release_id"]: r["value"] for r in rows if r["symbol"] == "revision_delta" and r["period"].year == 2009}
    assert delta == {
        "WEO-2009-APR": Decimal(0), "WEO-2009-OCT": Decimal("-6.398") - Decimal("-4.509"),
        "WEO-2010-APR": Decimal(0), "WEO-2010-OCT": Decimal("-13.584") - Decimal("-12.866"),
        "WEO-2025-OCT": Decimal("-15.424") - Decimal("-12.866"),
        "WEO-2026-APR": Decimal("-15.423608") - Decimal("-12.866"),
        "WEO-2026-OCT": Decimal("-15.423608") - Decimal("-12.866")}
    by_id = {c["citation_id"]: c["citation"] for c in citations}
    digest = {release: sha((ARCHIVE / name).read_bytes()) for name, release, *_ in ARCHIVE_RELEASES} | {
        "WEO-2025-OCT": sha((FIX / "imf_weo" / "weo_2025_oct_GRC_GGXCNL_NGDP_2000-2015.csv").read_bytes()),
        "WEO-2026-APR": sha((FIX / "imf_weo" / "weo_2026_apr_GRC_GGXCNL_NGDP_2000-2015.csv").read_bytes()),
        "WEO-2026-OCT": sha((FIX / "imf_weo" / GRC_CURRENT).read_bytes())}
    for row in rows:
        if row["symbol"] == "deficit_pct_gdp" and row["period"].year == 2009:
            (item,) = by_id[row["citation_id"]]["inputs"]
            assert (item["source_id"], item["release_id"], item["snapshot_sha256"]) == (
                IMF, row["release_id"], digest[row["release_id"]])


def test_greek_run_has_no_eurostat_input_and_lists_what_is_missing(v07_run):
    _, rows, citations, gaps = v07_run
    # Finding (matching, 903): see test_matching_findings_for_the_greek_indicators_are_visible. Without a Eurostat
    # binding the output has IMF rows only, so none of the plan's Eurostat values (B9/GD, 2009-2010 EDP notifications)
    # is reached; the sample holds Eurostat 2000-2015 for EL and the gaps below list exactly what V07 lacks.
    assert {r["source"] for r in rows} == {IMF}
    assert {i["source_id"] for c in citations for i in c["citation"]["inputs"]} == {IMF}
    assert {(g["symbol"], g["reason"]) for g in gaps} == {
        ("deficit_pct_gdp", "insufficient_sources"),  # 2 sources wanted, IMF only
        ("debt_eur", "unbound_indicator"), ("deficit_eur", "unbound_indicator"),
        ("debt_eur", "entity_unavailable"), ("deficit_eur", "entity_unavailable"),
        ("debt_eur", "insufficient_sources"), ("deficit_eur", "insufficient_sources"),
        ("debt_change", "no_aligned_inputs"), ("stock_flow_adjustment", "no_aligned_inputs")}
    assert {(g["symbol"], g["entity"]) for g in gaps if g["reason"] == "entity_unavailable"} == {
        ("debt_eur", "GRC"), ("deficit_eur", "GRC")}


# ---------------------------------------------------------------- license gate (real levels)


def _package(pilot, run, registry):
    root = pilot["root"]
    consumers = {c.id: c for c in load_consumers(root).consumers}
    with SQLiteDemandRegistry(root / "eltiro" / "demands.sqlite3") as demands:
        return package_run(run[0]["run_id"], data_root=root, registry=registry, consumers=consumers, demands=demands)


def _lineage(run):
    return {i["source_id"] for c in run[2] for i in c["citation"]["inputs"]}


def test_license_gate_refuses_the_whole_gdp_package_naming_the_no_outflow_sources(pilot, gdp_run):
    root = pilot["root"]
    before = sorted((root / "consumer" / "outbox").rglob("*"))
    with pytest.raises(LicenseGateError) as error:
        _package(pilot, gdp_run, pilot["real_registry"])
    # Lineage of the run: IMF (raw_ok), OECD and WDI (no_outflow). The refusal names exactly the no_outflow ones.
    assert _lineage(gdp_run) == {IMF, "intl-oecd", "intl-wdi"}
    # Finding (see the next test): `check_rows` raises at the first offending row, so only that row's no_outflow source
    # is named (today OECD, whose rows come first), never the raw_ok IMF and not WDI.
    assert (error.value.code, error.value.sources) == ("no_outflow", ("intl-oecd",))
    # Nothing was written: no data file and no partial bundle (receipts of intake are the only outbox files).
    assert sorted((root / "consumer" / "outbox").rglob("*")) == before


def test_license_gate_names_each_no_outflow_source_of_the_gdp_lineage(pilot, gdp_run):
    # Finding (Eltiro license gate, diagnostics): `check_rows` stops at the first row that has a no_outflow source, so
    # the whole-package refusal above names only that row's sources, not every no_outflow source of the lineage.
    # It is fail-closed (nothing is written), so this is a reporting gap, not a leak. Checking the rows of one source at
    # a time shows each source's own level under the real registry.
    root = pilot["root"]
    _, rows, citations, _ = gdp_run
    consumer = next(c for c in load_consumers(root).consumers if c.id == CONSUMER)
    with SQLiteDemandRegistry(root / "eltiro" / "demands.sqlite3") as demands:
        demand = next(d for d in demands.list_active() if d.id == "three-gdp-reconciliation")
    by_id = {c["citation_id"]: c for c in citations}
    for source in (IMF, "intl-oecd", "intl-wdi"):
        mine = [r for r in rows if r["source"] == source and r["symbol"] in ("gdp_usd", "gdp_lcu")]
        assert mine and all(by_id[r["citation_id"]]["citation"]["inputs"][0]["source_id"] == source for r in mine)
        kwargs = {"registry": pilot["real_registry"], "demand": demand, "consumer": consumer}
        if source == IMF:
            clean, _, summary = check_rows(mine, [by_id[r["citation_id"]] for r in mine], **kwargs)
            assert len(clean) == len(mine) and summary.by_source == {IMF: Outflow.raw_ok}
        else:
            with pytest.raises(LicenseGateError) as error:
                check_rows(mine, [by_id[r["citation_id"]] for r in mine], **kwargs)
            assert (error.value.code, error.value.sources) == ("no_outflow", (source,))


def test_greek_run_is_deliverable_only_because_eurostat_is_not_bound(pilot, v07_run):
    # Finding (matching, 903): the plan expects V07 to be refused as a whole once Eurostat (no_outflow) is in its
    # lineage. With the deterministic tier alone V07's lineage is IMF (raw_ok) only, so the real gate lets it out.
    root = pilot["root"]
    assert _lineage(v07_run) == {IMF}
    delivery = _package(pilot, v07_run, pilot["real_registry"])
    path = Path(delivery["path"])
    assert path.is_relative_to(root / "consumer" / "outbox")
    manifest = OutputManifest.model_validate_json((path / "manifest.json").read_bytes())
    assert manifest.models == []
    assert manifest.license_summary.by_source == {IMF: Outflow.raw_ok}
    assert {f.name for f in manifest.files} == {"data.parquet"}  # V07 asks for parquet only
    assert {i.source_id for c in manifest.citation_index.values() for i in c.inputs} == {IMF}
    provenance = json.loads((path / "provenance.json").read_bytes())
    assert len(provenance) == delivery["row_count"] > 0 and {p["source"] for p in provenance} == {IMF}


def test_imf_only_control_is_delivered_with_only_imf_values_and_provenance(pilot, control_run):
    gaps = control_run[3]
    delivery = _package(pilot, control_run, pilot["real_registry"])
    path = Path(delivery["path"])
    assert path.parent.parent.name == CONTROL_ID  # outbox/<demand>/v1/<run>
    manifest = OutputManifest.model_validate_json((path / "manifest.json").read_bytes())
    assert manifest.models == [] and manifest.license_summary.by_source == {IMF: Outflow.raw_ok}
    assert {f.name for f in manifest.files} >= {"data.parquet", "data.csv"}
    digest = sha((FIX / "imf_weo" / "weo_JPN-USA_NGDPD_2020-2030.csv").read_bytes())
    assert {(i.source_id, i.snapshot_sha256) for c in manifest.citation_index.values() for i in c.inputs} == {
        (IMF, digest)}
    expected = _imf_values("weo_JPN-USA_NGDPD_2020-2030.csv", "NGDPD", {"JPN", "USA"})
    provenance = json.loads((path / "provenance.json").read_bytes())
    delivered = {(p["country"], int(p["period"][:4])): Decimal(str(p["value"])) for p in provenance
                 if p["symbol"] == "gdp_usd"}
    assert delivered == expected and {p["source"] for p in provenance} == {IMF}
    table = pq.read_table(path / "data.parquet").to_pylist()
    assert len(table) == len(provenance) == delivery["row_count"] == 10
    assert not [g for g in gaps if g["reason"] == "unbound_indicator"]


def test_package_with_synthetic_raw_ok_license(pilot, gdp_run):
    """SYNTHETIC_RAW_OK: shows what a granted package contains; not the real license."""
    root = pilot["root"]
    synthetic = []
    for spec in pilot["specs"].values():
        # The registry only accepts raw_ok together with redistributable + allowed.
        license_info = spec.license.model_dump() | {
            "outflow": "raw_ok", "usage_scope": "redistributable", "redistribution": "allowed"}
        synthetic.append(type(spec).model_validate(spec.model_dump() | {"license": license_info}))
    delivery = _package(pilot, gdp_run, FakeRegistry(synthetic))
    path = Path(delivery["path"])
    assert path.is_relative_to(root / "consumer" / "outbox")
    manifest = OutputManifest.model_validate_json((path / "manifest.json").read_bytes())
    assert (manifest.demand_id, manifest.generation, manifest.generation_token) == (
        "three-gdp-reconciliation", 1, pilot["token"])
    assert manifest.models == []  # Checkpoint m1 runs without any model.
    assert manifest.license_summary.by_source == {IMF: Outflow.raw_ok, "intl-oecd": Outflow.raw_ok,
                                                  "intl-wdi": Outflow.raw_ok}
    assert {f.name for f in manifest.files} >= {"data.parquet", "data.csv"}
    provenance = json.loads((path / "provenance.json").read_bytes())
    assert len(provenance) == delivery["row_count"] > 0
    assert len(manifest.citation_index) == delivery["row_count"]


# ---------------------------------------------------------------- gaps and scorer


def test_gap_reports_and_scorer_state(pilot, gdp_run, v07_run):
    root = pilot["root"]
    consumers = {c.id: c for c in load_consumers(root).consumers}
    with (SQLiteDemandRegistry(root / "eltiro" / "demands.sqlite3") as demands,
          SQLiteProfileStore(root) as profiles, SQLiteMatchStore(root) as matches):
        active = {d.id: d for d in demands.list_active()}
        reports = {name: gap_report(active[name], 1, consumer=consumers[CONSUMER], matches=matches,
                                    profiles=profiles, registry=pilot["registry"], data_dir=root)
                   for name in ("three-gdp-reconciliation", "greece-deficit-revision-chain")}
        records = [r for name in reports for r in matches.for_demand(active[name].id, active[name].version, 1)]
    kinds = {name: {i["key"]: {g["kind"] for g in i["gaps"]} for i in report["indicators"]}
             for name, report in reports.items()}
    # Finding (Eltiro gaps, scope): `gap_report` works on bindings only (kind + reason per indicator: missing,
    # pending_confirmation, below_min_sources, ...), never on countries or points. So #002's report is empty although the
    # run's gaps.json lists DEU/FRA/GBR and the USA local-currency point; "which points are missing" is only in the run.
    assert all(set(g) == {"kind", "reason"} for report in reports.values() for i in report["indicators"]
               for g in i["gaps"])
    # #002: three sources are bound for gdp_usd, IMF + WDI for gdp_lcu (min 2): no indicator-level gap.
    assert kinds["three-gdp-reconciliation"] == {"gdp_usd": set(), "gdp_lcu": set()}
    # V07: only IMF is bound for the deficit ratio (2 sources wanted); debt and deficit in EUR have no candidate at all.
    assert kinds["greece-deficit-revision-chain"] == {
        "deficit_pct_gdp": {"below_min_sources"}, "debt_eur": {"missing", "below_min_sources"},
        "deficit_eur": {"missing", "below_min_sources"}}
    gold = [load_gold(GOLD / f"{name}.yaml") for name in reports]
    scored = score_matches(gold, records, load_thresholds(ROOT / "eval/eltiro/thresholds.yaml"))
    # All gold is pending_h2: nothing is evaluated, and the checkpoint cannot claim a pass.
    assert scored.overall.evaluated is False and scored.passed is False
    assert len(scored.skipped_gold) == sum(len(g.entries) for g in gold)
    # Which gold entries now have a recorded series behind them (for a person to decide on `verified`; eval/ is not
    # changed here). The gold SeriesIdentity (table_key, fixed_dimensions) still differs from the real ones, so none is a
    # drop-in. OECD gold entries have no code: their currency (USD/LCU) maps to UNIT_MEASURE USD_EXC/XDC of B1GQ.
    live = {(p.series.source_id, p.series.indicator_code, p.series.table_key, p.series.fixed_dimensions.get("unit"),
             p.series.fixed_dimensions.get("UNIT_MEASURE")) for p in pilot["profiles"]}

    def recorded_series(series):
        if series.source_id == "intl-oecd":
            unit = {"USD": "USD_EXC", "LCU": "XDC"}[series.fixed_dimensions["currency"]]
            return ("intl-oecd", "B1GQ", "df_table1_expenditure", None, unit) in live
        if series.source_id == "eu-eurostat":
            return (series.source_id, series.indicator_code, series.table_key, series.fixed_dimensions["unit"], None) in live
        return any(item[:2] == (series.source_id, series.indicator_code) for item in live)

    recorded = sorted(f"{g.demand_id}/{entry.id}" for g in gold for entry in g.entries if recorded_series(entry.series))
    # Entries on series that are not recorded have none: WDI growth/per-capita/PPP/constant LCU, Eurostat gov_10a_main
    # (central government, interest) and IMF GGXONLB_NGDP (primary balance); all are negative examples.
    assert recorded == [
        "greece-deficit-revision-chain/debt-ratio", "greece-deficit-revision-chain/eurostat-debt_eur",
        "greece-deficit-revision-chain/eurostat-deficit_eur", "greece-deficit-revision-chain/eurostat-deficit_pct_gdp",
        "greece-deficit-revision-chain/imf-balance", "greece-deficit-revision-chain/imf-forecast",
        "three-gdp-reconciliation/oecd-gdp_lcu", "three-gdp-reconciliation/oecd-gdp_usd",
        "three-gdp-reconciliation/wdi-gdp_lcu", "three-gdp-reconciliation/wdi-gdp_usd",
        "three-gdp-reconciliation/weo-forecast", "three-gdp-reconciliation/weo-gdp_lcu",
        "three-gdp-reconciliation/weo-gdp_usd", "three-gdp-reconciliation/wrong-currency"]
    evidence = _evidence_dir()
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "wisteria-m1.json").write_text(json.dumps({
        "generation_token": pilot["token"], "intake": pilot["intake"], "receipts": pilot["receipts"],
        "gdp_gaps": sorted({(g["symbol"], g["reason"]) for g in gdp_run[3]}),
        "greece_gaps": sorted({(g["symbol"], g["reason"]) for g in v07_run[3]}),
        "gap_report_kinds": {n: {k: sorted(v) for k, v in item.items()} for n, item in kinds.items()},
        "gold_entries_with_recorded_series": recorded,
        "scorer": scored.model_dump(mode="json"),
    }, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
