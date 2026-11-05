"""WISTERIA checkpoint m1 (902), offline part: pilot demands from injection to output, no model.

Real recorded bytes (tests/fixtures/wdi, tests/fixtures/eurostat) go through the real
parsers and stores into one retained Rikolto generation; Eltiro then runs intake,
profiles, deterministic matching, a simulated human confirmation, fixed-generation
execution, packaging behind the license gate, gap reports and the offline scorer.

What this test can NOT show (recorded in the evidence summary, decided by H2/H3/H4):
- V13 (monthly labour survey) has no recorded table: tests/fixtures/estat_api/README.md
  says the statsDataId of table 4 is still undecided and was not recorded.
- IMF, OECD, ILO, UN COMTRADE and WTO are not registered (WISTERIA 302–306 wait for H2),
  so the pilot demand files as written are rejected at intake.
- Every gold entry and value point is pending_h2, so no real precision/recall exists yet.
- Both registered pilot sources are no_outflow until H2 verifies redistribution.
The one place where an outflow is granted is ``SYNTHETIC_RAW_OK``: a test-only
registry copy that never describes the real license.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
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
from datumoj.eltiro.license_gate import LicenseGateError
from datumoj.eltiro.match_store import SQLiteMatchStore
from datumoj.eltiro.matching import match_demand
from datumoj.eltiro.models import OutputManifest
from datumoj.eltiro.package import package_run
from datumoj.eltiro.profile_store import SQLiteProfileStore
from datumoj.eltiro.profiles import generate_profiles
from datumoj.eltiro.registry_store import SQLiteDemandRegistry
from datumoj.rikolto.core.models import Outflow, SnapshotOrder, StoreCopy
from datumoj.rikolto.core.registry import load_registry
from datumoj.rikolto.parsers.eurostat import EurostatParser, read_structure
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
REGISTERED = ("eu-eurostat", "intl-wdi")
# Evidence goes to a temporary directory unless explicitly requested (never tracked paths).
EVIDENCE = (Path(os.environ["DATUMOJ_EVIDENCE_DIR"]) if os.environ.get("DATUMOJ_EVIDENCE_DIR")
            else Path(tempfile.mkdtemp(prefix="datumoj-wisteria-902-")))


def _recording(folder, name):
    entry = json.loads((FIX / folder / "_responses.json").read_bytes())[name]
    entry = entry.get("extracted_from", entry)
    body = (FIX / folder / name).read_bytes()
    return entry, body


def _parsed(specs):
    """Real parsers over recorded bytes; WDI pages are joined exactly like MAPLE's evaluation."""
    out = []
    first, page1 = _recording("wdi", "gdp_cd_cn_JPN-USA_2020-2024_p1.json")
    _, page2 = _recording("wdi", "gdp_cd_cn_JPN-USA_2020-2024_p2.json")
    rows = json.loads(page1, parse_float=Decimal)[1] + json.loads(page2, parse_float=Decimal)[1]
    body = json.dumps([{"page": 1, "pages": 1, "total": len(rows)}, rows], default=str).encode()
    snapshot = make_snapshot("intl-wdi", first["url"], body, fetched_at=datetime.fromisoformat(first["fetched_at"]))
    out.append(("intl-wdi", snapshot, WDIParser().parse(snapshot, body, {
        "request_type": "data", "countries": ["JPN", "USA"], "start_year": 2020, "end_year": 2024,
        "indicators": specs["intl-wdi"].scope["indicators"]})))
    entry, body = _recording("wdi", "indicator_NY.GDP.MKTP.CN.json")
    snapshot = make_snapshot("intl-wdi", entry["url"], body, fetched_at=datetime.fromisoformat(entry["fetched_at"]))
    out.append(("intl-wdi", snapshot, WDIParser().parse(
        snapshot, body, {"request_type": "metadata", "indicator": "NY.GDP.MKTP.CN"})))
    for code, name in (("gov_10dd_edpt1", "gov_10dd_edpt1_EL_2000-2015.xml"),
                       ("nama_10_gdp", "nama_10_gdp_DE-FR-NL_2020-2024.xml")):
        _, structure = _recording("eurostat", f"datastructure_{code.upper()}.xml")
        entry, body = _recording("eurostat", name)
        snapshot = make_snapshot("eu-eurostat", entry["url"], body,
                                 fetched_at=datetime.fromisoformat(entry["fetched_at"]))
        out.append(("eu-eurostat", snapshot, EurostatParser().parse(
            snapshot, body, {"structure": read_structure(structure, code)})))
    return out, rows


def _publish(root, parsed):
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
    copies = []
    for path in [root / "catalog.sqlite", root / "chunks.sqlite", *sorted((root / "parquet").glob("*/*.parquet"))]:
        digest = sha(path.read_bytes())
        copies.append(StoreCopy(name=path.relative_to(root).as_posix(), key=f"copies/sha256/{digest}", sha256=digest,
                                size_bytes=path.stat().st_size,
                                format="sqlite" if path.suffix == ".sqlite" else "parquet"))
    generation = make_state_generation(1).model_copy(update={"stores": copies})
    return retain_generation(root, generation.model_dump_json().encode(), root)


def _existing_part(name):
    """The checkpoint's "parts with data already in the store": registered preferences, no model."""
    data = yaml.safe_load((DEMANDS / name).read_text(encoding="utf-8"))
    for indicator in data["indicators"]:
        indicator["preferred_sources"] = [s for s in indicator.get("preferred_sources", []) if s in REGISTERED]
        indicator["min_sources"] = 1
    data["models"] = {"max_tier": "none", "allow_cloud": False}
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False).encode()


@pytest.fixture(scope="module")
def pilot(tmp_path_factory):
    def forbidden(*args, **kwargs):
        pytest.fail("checkpoint m1 must never access the network")

    root = tmp_path_factory.mktemp("wisteria-m1").resolve()
    registry_all = load_registry(ROOT / "sources")
    specs = {sid: registry_all.get(sid) for sid in REGISTERED}
    registry = FakeRegistry(list(specs.values()))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
        patch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
        parsed, wdi_rows = _parsed(specs)
        token = _publish(root, parsed)
        (root / "eltiro").mkdir()
        (root / "eltiro" / "consumers.yaml").write_text(yaml.safe_dump({"consumers": [{
            "id": CONSUMER, "inbox": "consumer/inbox", "outbox": "consumer/outbox",
            "allowed_sources": list(REGISTERED), "accept_outflow": ["raw_ok", "derived_only"],
            "models": {"consumer": CONSUMER, "authorized_sources": list(REGISTERED), "max_tier": "low"},
            "max_active_demands": 10, "max_calls_per_run": 200}]}), encoding="utf-8")
        inbox = root / "consumer" / "inbox"
        inbox.mkdir(parents=True)
        # 1. The pilot demand files exactly as written (all four cases).
        for path in sorted(DEMANDS.glob("*.yaml")):
            (inbox / path.name).write_bytes(path.read_bytes())
        with SQLiteDemandRegistry(root / "eltiro" / "demands.sqlite3") as demands:
            original = IntakeService(root, demands, registry).scan()
            original_receipts = {json.loads(p.read_text())["demand_id"]: json.loads(p.read_text())["reason_code"]
                                 for p in (root / "consumer" / "outbox").glob("*.json")}
            # 2. The existing-data part of #002 and V07, under their own ids/versions.
            for name in ("three-gdp-reconciliation.yaml", "greece-deficit-revision-chain.yaml"):
                (inbox / name).write_bytes(_existing_part(name))
            existing = IntakeService(root, demands, registry).scan()
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
        yield {"root": root, "token": token, "registry": registry, "specs": specs, "wdi_rows": wdi_rows,
               "original": original, "original_receipts": original_receipts, "existing": existing,
               "profiles": profile_list, "results": results}


def test_pilot_demands_as_written_wait_for_unregistered_sources(pilot):
    # Every pilot names an IMF/OECD/ILO/COMTRADE/WTO preference; 302–306 are not registered yet.
    assert pilot["original"] == {"accepted": 0, "rejected": 4}
    assert pilot["original_receipts"] == {
        "greece-deficit-revision-chain": "source_forbidden", "japan-monthly-earnings-revisions": "source_forbidden",
        "three-gdp-reconciliation": "source_forbidden", "trade-mirror": "source_forbidden"}
    # The existing-data parts are accepted under the same ids (version 1 was never registered).
    assert pilot["existing"] == {"accepted": 2, "rejected": 0}


def test_profiles_cover_the_published_generation(pilot):
    wdi = {p.series.indicator_code: p for p in pilot["profiles"] if p.series.source_id == "intl-wdi"}
    assert set(wdi) == {"NY.GDP.MKTP.CD", "NY.GDP.MKTP.CN"}
    assert all(p.availability == "observations" and p.frequency == "annual" for p in wdi.values())
    assert wdi["NY.GDP.MKTP.CD"].areas == ["JPN", "USA"]
    # Scale is not invented: H2 must supply the publication multiplier.
    assert all(p.scale.multiplier is None for p in pilot["profiles"])


def test_deterministic_matching_queues_candidates_and_never_auto_binds(pilot):
    gdp = pilot["results"]["three-gdp-reconciliation"]
    # vector=False and model=False: only the deterministic tier runs.
    assert {r.tier for r in gdp.records} == {"deterministic"}
    assert {(r.indicator_key, r.series.indicator_code) for r in gdp.records} == {
        (key, code) for key in ("gdp_usd", "gdp_lcu") for code in ("NY.GDP.MKTP.CD", "NY.GDP.MKTP.CN")}
    # Unit text differs ("current US$" vs "USD"), so nothing auto-binds: a person decides.
    assert not any(r.auto_bind for r in gdp.records)
    assert len(gdp.queue) == len(gdp.records) == 4


def test_matching_quality_findings_are_visible(pilot):
    """Findings for H2/H4, asserted so a change in behaviour is noticed, not hidden."""
    gdp = pilot["results"]["three-gdp-reconciliation"]
    usd = sorted((r for r in gdp.records if r.indicator_key == "gdp_usd"), key=lambda r: -r.confidence)
    # The LCU series currently outranks the US$ series for gdp_usd (keyword overlap only).
    assert usd[0].series.indicator_code == "NY.GDP.MKTP.CN"
    greece = pilot["results"]["greece-deficit-revision-chain"]
    # Eurostat rows carry codes only (B9/GD; the recorded DSD has no codelists), so the
    # deterministic tier finds no Eurostat candidate and proposes unrelated WDI GDP instead.
    assert {r.series.source_id for r in greece.records} == {"intl-wdi"}
    assert not any(r.auto_bind for r in greece.records)


@pytest.mark.xfail(strict=True, reason=(
    "Known defect: EurostatParser stores TIME_PERIOD (and geo) in dims, and Eltiro series identity is "
    "source+table+indicator+code+all dims, so one Eurostat series becomes one profile per year. "
    "eval/eltiro/gold/greece-deficit-revision-chain.yaml expects {freq, geo, sector, unit}."))
def test_eurostat_series_identity_does_not_split_by_period(pilot):
    deficit = [p for p in pilot["profiles"] if p.series.source_id == "eu-eurostat"
               and p.series.table_key == "gov_10dd_edpt1" and p.series.indicator_code == "B9"
               and p.series.fixed_dimensions.get("unit") == "PC_GDP"]
    assert len(deficit) == 1


def _confirm_wdi_and_run(pilot):
    root = pilot["root"]
    with SQLiteDemandRegistry(root / "eltiro" / "demands.sqlite3") as demands, SQLiteMatchStore(root) as matches:
        demand = next(d for d in demands.list_active() if d.id == "three-gdp-reconciliation")
        wanted = {"gdp_usd": "NY.GDP.MKTP.CD", "gdp_lcu": "NY.GDP.MKTP.CN"}
        # Simulated human decision (as `eltiro matches confirm/reject` would record it).
        for record in matches.for_demand(demand.id, demand.version, 1):
            decision = "confirmed" if record.series.indicator_code == wanted[record.indicator_key] else "rejected"
            matches.set_confirmation(demand.id, demand.version, record.indicator_key, record.series, 1, decision)
        record = run_registered(demand.id, generation_token=pilot["token"], data_root=root,
                                registry=pilot["registry"], demands=demands)
    run = root / "eltiro" / "runs" / record["run_id"]
    rows = pq.read_table(run / "data.parquet").to_pylist()
    citations = json.loads((run / "citations.json").read_bytes())
    gaps = json.loads((run / "gaps.json").read_bytes())
    return record, rows, citations, gaps


@pytest.fixture(scope="module")
def gdp_run(pilot):
    return _confirm_wdi_and_run(pilot)


def test_fixed_generation_run_matches_recorded_values_with_citations(pilot, gdp_run):
    record, rows, citations, gaps = gdp_run
    assert record["generation"] == 1 and record["generation_token"] == pilot["token"]
    expected = {}
    codes = {"NY.GDP.MKTP.CD": "gdp_usd", "NY.GDP.MKTP.CN": "gdp_lcu"}
    for item in pilot["wdi_rows"]:
        if item["value"] is not None:
            expected[(codes[item["indicator"]["id"]], item["countryiso3code"], item["date"])] = Decimal(str(item["value"]))
    raw = {(r["symbol"], r["country"], str(r["period"].year)): r["value"] for r in rows
           if r["symbol"] in ("gdp_usd", "gdp_lcu")}
    # Every value equals the recorded source byte-for-byte as Decimal (source original, not consensus).
    assert raw == expected
    assert len(raw) == 20
    by_id = {c["citation_id"]: c["citation"] for c in citations}
    for row in rows:
        if row["symbol"] in ("gdp_usd", "gdp_lcu"):
            inputs = by_id[row["citation_id"]]["inputs"]
            assert [i["source_id"] for i in inputs] == ["intl-wdi"]
            assert all(len(i["snapshot_sha256"]) == 64 for i in inputs)
    reasons = {(g["symbol"], g["reason"]) for g in gaps}
    # Countries absent from the recording and unconfirmed publication scale are explicit gaps.
    assert {("gdp_usd", "entity_unavailable"), ("gdp_lcu", "entity_unavailable"),
            ("gdp_usd_units", "unconfirmed_scale"), ("gdp_lcu_units", "unconfirmed_scale")} <= reasons
    assert {g["entity"] for g in gaps if g["reason"] == "entity_unavailable"} == {"DEU", "FRA", "GBR"}


def test_license_gate_refuses_whole_package_until_h2(pilot, gdp_run):
    root = pilot["root"]
    record = gdp_run[0]
    consumers = {c.id: c for c in load_consumers(root).consumers}
    before = sorted(p.relative_to(root) for p in (root / "consumer" / "outbox").rglob("*"))
    with SQLiteDemandRegistry(root / "eltiro" / "demands.sqlite3") as demands, pytest.raises(LicenseGateError) as error:
        package_run(record["run_id"], data_root=root, registry=pilot["registry"], consumers=consumers, demands=demands)
    assert (error.value.code, error.value.sources) == ("no_outflow", ("intl-wdi",))
    assert sorted(p.relative_to(root) for p in (root / "consumer" / "outbox").rglob("*")) == before


def test_package_with_synthetic_raw_ok_license(pilot, gdp_run):
    """SYNTHETIC_RAW_OK: shows what a granted package contains; not the real license."""
    root = pilot["root"]
    record = gdp_run[0]
    synthetic = []
    for spec in pilot["specs"].values():
        # The registry only accepts raw_ok together with redistributable + allowed.
        license_info = spec.license.model_dump() | {
            "outflow": "raw_ok", "usage_scope": "redistributable", "redistribution": "allowed"}
        synthetic.append(type(spec).model_validate(spec.model_dump() | {"license": license_info}))
    consumers = {c.id: c for c in load_consumers(root).consumers}
    with SQLiteDemandRegistry(root / "eltiro" / "demands.sqlite3") as demands:
        delivery = package_run(record["run_id"], data_root=root, registry=FakeRegistry(synthetic),
                               consumers=consumers, demands=demands)
    path = Path(delivery["path"])
    assert path.is_relative_to(root / "consumer" / "outbox")
    manifest = OutputManifest.model_validate_json((path / "manifest.json").read_bytes())
    assert (manifest.demand_id, manifest.generation, manifest.generation_token) == (
        "three-gdp-reconciliation", 1, pilot["token"])
    assert manifest.models == []  # Checkpoint m1 runs without any model.
    assert manifest.license_summary.by_source == {"intl-wdi": Outflow.raw_ok}
    assert {f.name for f in manifest.files} >= {"data.parquet", "data.csv"}
    provenance = json.loads((path / "provenance.json").read_bytes())
    assert len(provenance) == delivery["row_count"] > 0
    assert len(manifest.citation_index) == delivery["row_count"]


def test_gap_reports_and_scorer_state(pilot):
    root = pilot["root"]
    consumers = {c.id: c for c in load_consumers(root).consumers}
    with (SQLiteDemandRegistry(root / "eltiro" / "demands.sqlite3") as demands,
          SQLiteProfileStore(root) as profiles, SQLiteMatchStore(root) as matches):
        greece = next(d for d in demands.list_active() if d.id == "greece-deficit-revision-chain")
        report = gap_report(greece, 1, consumer=consumers[CONSUMER], matches=matches, profiles=profiles,
                            registry=pilot["registry"], data_dir=root)
        records = [r for d in demands.list_active() for r in matches.for_demand(d.id, d.version, 1)]
    kinds = {i["key"]: {g["kind"] for g in i["gaps"]} for i in report["indicators"]}
    # Only unrelated WDI candidates exist for deficit_pct_gdp; debt/deficit in EUR have none.
    assert "pending_confirmation" in kinds["deficit_pct_gdp"]
    assert kinds["debt_eur"] >= {"missing"} and kinds["deficit_eur"] >= {"missing"}
    gold = [load_gold(GOLD / f"{name}.yaml") for name in ("three-gdp-reconciliation", "greece-deficit-revision-chain")]
    scored = score_matches(gold, records, load_thresholds(ROOT / "eval/eltiro/thresholds.yaml"))
    # All gold is pending_h2: nothing is evaluated, and the checkpoint cannot claim a pass.
    assert scored.overall.evaluated is False and scored.passed is False
    assert len(scored.skipped_gold) == sum(len(g.entries) for g in gold)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "wisteria-m1-offline.json").write_text(json.dumps({
        "generation_token": pilot["token"], "original_intake": pilot["original_receipts"],
        "existing_part_intake": pilot["existing"],
        "greece_gaps": {key: sorted(value) for key, value in kinds.items()},
        "scorer": scored.model_dump(mode="json"),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
