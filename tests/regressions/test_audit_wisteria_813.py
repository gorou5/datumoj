"""813 policy and series alignment checks using only synthetic local runs."""

from datetime import date
from decimal import Decimal
from hashlib import sha256
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from datumoj.eltiro.license_gate import LicenseGateError
from datumoj.eltiro.models import MAX_LABEL_LENGTH
from datumoj.eltiro.package import package_run
from datumoj.eltiro.testing.samples import make_demand
from tests.eltiro.test_executor import consumer
from tests.eltiro.test_package import context, delivered, sources, write_run

SERIES_A = 'SYNTHETIC_PRIVATE_SERIES_A_国内総生産'
SERIES_B = 'SYNTHETIC_PRIVATE_SERIES_B_別の次元'
NOTE = 'SYNTHETIC_PRIVATE_NOTE'
UNIT = 'SYNTHETIC_ALLOWED_UNIT_億円'


def digest(series):
    return sha256(series.encode("utf-8")).hexdigest()[:16]


def setup(root, level="derived_only"):
    demand = make_demand(output={"fields": ["gdp", "unit", "series", "note", "citation_id"],
                                 "format": ["csv", "parquet"]},
                         accept_outflow=["raw_ok", "derived_only"])
    return context(root, level, demand=demand)


def run(root, entries, *, updates=None):
    record = write_run(root, values=[value for _, value in entries])
    file = root / "eltiro/runs" / record["run_id"] / "data.parquet"
    table = pq.read_table(file)
    rows = table.to_pylist()
    for i, ((series, _), row) in enumerate(zip(entries, rows, strict=True)):
        row.update(series=series, period=date(2020, 1, 1), unit=UNIT, note=NOTE)
        if updates:
            row.update(updates[i])
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), file)
    return record["run_id"]


def read_diff(path):
    return json.loads((path / "diff.json").read_bytes())


@pytest.mark.parametrize("level", ["raw_ok", "derived_only"])
def test_allowed_columns_and_bundle_wide_source_text_policy(tmp_path, level):
    ctx = setup(tmp_path, level)
    run_id = run(tmp_path, [(SERIES_A, "10")])
    path = package_run(run_id, **ctx)["path"]
    row = pq.read_table(path / "data.parquet").to_pylist()[0]
    assert row["unit"] == UNIT and row["gdp"] == Decimal("10")
    assert UNIT in (path / "data.csv").read_text()
    if level == "raw_ok":
        assert row["series"] == SERIES_A and row["note"] == NOTE
    else:
        assert set(row) == {"gdp", "unit", "citation_id"}
        for file in path.iterdir():
            assert SERIES_A.encode() not in file.read_bytes()
            assert NOTE.encode() not in file.read_bytes()
    diff_bytes = (path / "diff.json").read_bytes()
    assert SERIES_A.encode() not in diff_bytes and NOTE.encode() not in diff_bytes
    assert read_diff(path)["added"][0]["series_sha256"] == digest(SERIES_A)
    provenance = json.loads((path / "provenance.json").read_bytes())
    assert provenance[0]["series_sha256"] == digest(SERIES_A)
    before = delivered(path)
    times = {p.name: p.stat().st_mtime_ns for p in path.iterdir()}
    assert package_run(run_id, **ctx)["path"] == path
    assert delivered(path) == before
    assert times == {p.name: p.stat().st_mtime_ns for p in path.iterdir()}


def test_mixed_rows_keep_units_but_only_raw_rows_keep_text(tmp_path):
    ctx = setup(tmp_path)
    ctx["registry"] = sources({"intl-wdi": "raw_ok", "intl-imf-weo": "derived_only"})
    ctx["consumers"] = {"example-consumer": consumer(("intl-wdi", "intl-imf-weo"),
                                                    accept_outflow=["raw_ok", "derived_only"])}
    run_id = run(tmp_path, [(SERIES_A, "10"), (SERIES_B, "20")],
                 updates=[{"note": "RAW_NOTE"}, {"source": "intl-imf-weo"}])
    file = tmp_path / "eltiro/runs" / run_id / "citations.json"
    citations = json.loads(file.read_bytes())
    citations[1]["citation"]["inputs"][0]["source_id"] = "intl-imf-weo"
    file.write_text(json.dumps(citations))
    path = package_run(run_id, **ctx)["path"]
    rows = pq.read_table(path / "data.parquet").to_pylist()
    assert rows[0]["series"] == SERIES_A and rows[0]["note"] == "RAW_NOTE"
    assert rows[1]["series"] is None and rows[1]["note"] is None
    assert [r["unit"] for r in rows] == [UNIT, UNIT]
    for file in path.iterdir():
        assert SERIES_B.encode() not in file.read_bytes() and NOTE.encode() not in file.read_bytes()
    # Tightening one input still blocks the whole next bundle.
    before = delivered(tmp_path / "consumer/outbox")
    ctx["registry"] = sources({"intl-wdi": "raw_ok", "intl-imf-weo": "no_outflow"})
    with pytest.raises(LicenseGateError, match="no_outflow"):
        package_run(run_id, **ctx)
    assert delivered(tmp_path / "consumer/outbox") == before


def test_no_outflow_never_creates_any_bundle(tmp_path):
    ctx = setup(tmp_path, "no_outflow")
    run_id = run(tmp_path, [(SERIES_A, "10")])
    with pytest.raises(LicenseGateError, match="no_outflow"):
        package_run(run_id, **ctx)
    assert not (tmp_path / "consumer").exists()


@pytest.mark.parametrize("level", ["raw_ok", "derived_only"])
@pytest.mark.parametrize("unit", ["u" * (MAX_LABEL_LENGTH + 1), "u\nv", "u\rv", "u\tv", "u\x00v",
                                 "u\x7fv", "u\x85v", "u\u2028v", "u\u202ev"])
def test_invalid_unit_is_blank_and_safely_audited(tmp_path, level, unit):
    ctx = setup(tmp_path, level)
    run_id = run(tmp_path, [(SERIES_A, "10"), (SERIES_B, "20")],
                 updates=[{"unit": unit}, {"unit": UNIT}])
    path = package_run(run_id, **ctx)["path"]
    assert [r["unit"] for r in pq.read_table(path / "data.parquet").to_pylist()] == [None, UNIT]
    audit_file = tmp_path / "eltiro/runs" / (run_id + ".package-audit.json")
    audit = json.loads(audit_file.read_bytes())
    assert audit["status"] == "allowed"
    assert audit["sanitizations"] == [{"citation_id": "row-0", "column": "unit", "code": "invalid_unit"}]
    assert SERIES_A not in audit_file.read_text() and NOTE not in audit_file.read_text()
    before = delivered(path)
    package_run(run_id, **ctx)
    assert delivered(path) == before and json.loads(audit_file.read_bytes()) == audit


def test_unit_length_boundary_and_missing_value_are_kept(tmp_path):
    ctx = setup(tmp_path)
    unit = "円" * MAX_LABEL_LENGTH
    run_id = run(tmp_path, [(SERIES_A, None), (SERIES_B, "0")], updates=[{"unit": unit}, {"unit": None}])
    path = package_run(run_id, **ctx)["path"]
    rows = pq.read_table(path / "data.parquet").to_pylist()
    assert rows[0]["unit"] == unit and rows[0]["gdp"] is None
    assert rows[1]["unit"] is None and rows[1]["gdp"] == Decimal("0")
    audit = json.loads((tmp_path / "eltiro/runs" / (run_id + ".package-audit.json")).read_bytes())
    assert audit["sanitizations"] == []


def test_same_source_series_align_independently_even_when_reordered(tmp_path):
    ctx = setup(tmp_path)
    first = run(tmp_path, [(SERIES_A, "10"), (SERIES_B, "20")])
    package_run(first, **ctx)
    second = run(tmp_path, [(SERIES_B, "22"), (SERIES_A, "10")])
    path = package_run(second, **ctx)["path"]
    diff = read_diff(path)
    assert diff["comparable"] and diff["previous_run_id"] == first
    assert diff["added"] == diff["deleted"] == diff["incomparable"] == []
    assert len(diff["changed"]) == 1
    change = diff["changed"][0]
    assert change["before"]["value"] == "20" and change["after"]["value"] == "22"
    assert change["before"]["series_sha256"] == change["after"]["series_sha256"] == digest(SERIES_B)
    assert change["before"]["citation_id"] == "previous_row_1"
    third = run(tmp_path, [(SERIES_A, None), ("SYNTHETIC_SERIES_C", "30")])
    path = package_run(third, **ctx)["path"]
    diff = read_diff(path)
    assert [r["series_sha256"] for r in diff["added"]] == [digest("SYNTHETIC_SERIES_C")]
    assert [r["series_sha256"] for r in diff["deleted"]] == [digest(SERIES_B)]
    assert len(diff["changed"]) == 1 and diff["changed"][0]["after"]["value"] is None
    for file in path.iterdir():
        assert SERIES_A.encode() not in file.read_bytes() and SERIES_B.encode() not in file.read_bytes()
    before = delivered(path)
    package_run(third, **ctx)
    assert delivered(path) == before


def test_exact_duplicate_series_identity_still_fails_closed(tmp_path):
    ctx = setup(tmp_path)
    run_id = run(tmp_path, [(SERIES_A, "10"), (SERIES_A, "20")])
    with pytest.raises(ValueError, match="ambiguous difference identity"):
        package_run(run_id, **ctx)
    assert not (tmp_path / "consumer").exists()


def legacy_provenance(path):
    file = path / "provenance.json"
    rows = json.loads(file.read_bytes())
    for row in rows:
        del row["series_sha256"]
    file.write_text(json.dumps(rows))


def test_805_unique_key_compares_without_guessing_series_identity(tmp_path):
    ctx = setup(tmp_path)
    first = run(tmp_path, [(SERIES_A, "10")])
    path = package_run(first, **ctx)["path"]
    legacy_provenance(path)
    second = run(tmp_path, [(SERIES_A, "12")])
    diff = read_diff(package_run(second, **ctx)["path"])
    assert diff["comparable"] and diff["incomparable"] == []
    assert diff["added"] == diff["deleted"] == [] and len(diff["changed"]) == 1
    before, after = diff["changed"][0]["before"], diff["changed"][0]["after"]
    assert before["value"] == "10" and before["series_sha256"] is None
    assert after["value"] == "12" and after["series_sha256"] == digest(SERIES_A)


@pytest.mark.parametrize("old_count,new_count", [(2, 2), (1, 2), (2, 1), (2, 0)])
def test_805_ambiguous_group_is_reported_while_unique_groups_compare(tmp_path, old_count, new_count):
    ctx = setup(tmp_path)
    old_entries = [(SERIES_A, "10"), (SERIES_B, "20")][:old_count] + [("UNIQUE_SERIES", "30")]
    first = run(tmp_path, old_entries,
                updates=[{}] * old_count + [{"period": date(2021, 1, 1)}])
    path = package_run(first, **ctx)["path"]
    legacy_provenance(path)
    new_entries = [(SERIES_A, "11"), (SERIES_B, "21")][:new_count] + [("UNIQUE_SERIES", "31")]
    second = run(tmp_path, new_entries,
                 updates=[{}] * new_count + [{"period": date(2021, 1, 1)}])
    diff = read_diff(package_run(second, **ctx)["path"])
    assert diff["previous_run_id"] == first and not diff["comparable"]
    assert diff["added"] == diff["deleted"] == []
    assert len(diff["changed"]) == 1 and diff["changed"][0]["before"]["value"] == "30"
    assert diff["changed"][0]["after"]["value"] == "31"
    assert len(diff["incomparable"]) == 1
    assert diff["incomparable"][0]["message"] == "上次交付无法逐序列比较"
    assert diff["incomparable"][0]["reason"] == "previous_delivery_not_comparable_by_series"
    assert SERIES_A not in json.dumps(diff, ensure_ascii=False) and SERIES_B not in json.dumps(diff, ensure_ascii=False)
