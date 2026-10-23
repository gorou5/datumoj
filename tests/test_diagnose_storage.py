"""Synthetic local storage only; content fingerprints include all table files."""

import hashlib
import json
import sqlite3

import lancedb
import pytest
from typer.testing import CliRunner

from datumoj import cli, diagnose, wiring


def fingerprint(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def rows(start, count):
    return [{"id": i, "vector": [float(i), 1., 2., 3.]} for i in range(start, start + count)]


def test_storage_cli_counts_and_never_writes(tmp_path, monkeypatch):
    root = tmp_path / "data"
    db = lancedb.connect(root / "vectors")
    table = db.create_table("chunks", data=rows(0, 100))
    table.add(rows(100, 10))
    # Overwrite leaves historical data without running compaction or cleanup.
    table = db.create_table("chunks", data=rows(200, 30), mode="overwrite")
    table.delete("id < 210")
    db.create_table("chunks_g1", data=rows(0, 40))
    db.create_table("chunks_g2", data=rows(0, 50))
    with sqlite3.connect(root / "chunks.sqlite") as sql:
        sql.execute("CREATE TABLE sample (value INTEGER)")
    (root / "raw").mkdir()
    (root / "raw" / "synthetic").write_bytes(b"private synthetic content")
    (root / "vectors" / "_meta.json").write_text('{"generation": 2, "model": "private"}')
    (root / "vectors-state.json").write_text('{}')
    before = fingerprint(root)

    def forbidden(*args, **kwargs):
        pytest.fail("storage diagnosis must not construct stores or modify tables")

    monkeypatch.setattr(wiring, "build", forbidden)
    for method in ("optimize", "compact_files", "cleanup_old_versions", "restore"):
        monkeypatch.setattr(lancedb.table.LanceTable, method, forbidden)
    monkeypatch.setenv("DATUMOJ_DATA_DIR", str(root))
    runner = CliRunner()
    outputs = [runner.invoke(cli.app, args) for args in (
        ["diagnose", "storage"], ["diagnose", "storage", "--data-dir", str(root)],
        ["--data-dir", str(root), "diagnose", "storage"],
    )]
    for output in outputs:
        assert output.exit_code == 0, output.output
        assert str(root) not in output.output
        assert "private" not in output.output
    result = json.loads(outputs[0].output)
    assert result["read_only"] is True
    assert result["total_bytes"] == sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    assert sum(result["entries"].values()) == result["total_bytes"]
    tables = {t["table"]: t for t in result["vector_tables"]}
    report = tables["chunks"]
    assert report["status"] == "ok"
    assert (report["rows"], report["versions"], report["data_files"]) == (20, 4, 3)
    assert report["data_bytes"] == sum(p.stat().st_size for p in (root / "vectors/chunks.lance/data").iterdir())
    assert report["historical_or_unreferenced_data_bytes_estimate"] > 0
    assert report["deleted_rows"] == 10
    assert report["deleted_data_bytes_estimate"] == round(report["latest_referenced_data_bytes"] / 3)
    assert report["historical_manifest_bytes"] > 0
    assert report["reclaimable_bytes_estimate"] == (
        report["historical_or_unreferenced_data_bytes_estimate"]
        + report["historical_manifest_bytes"] + report["deleted_data_bytes_estimate"])
    assert 0 < report["retained_bytes_estimate"] < report["bytes"]
    assert [(tables[n]["generation"], tables[n]["rows"]) for n in ("chunks_g1", "chunks_g2")] == [(1, 40), (2, 50)]
    assert fingerprint(root) == before
    print(json.dumps({"synthetic_storage": result}, sort_keys=True))


def test_empty_missing_and_corrupt_directories(tmp_path):
    assert diagnose.storage(tmp_path)["total_bytes"] == 0
    missing = tmp_path / "missing"
    output = CliRunner().invoke(cli.app, ["diagnose", "storage", "--data-dir", str(missing)])
    assert output.exit_code == 1
    assert not missing.exists()
    assert str(missing) not in output.output
    broken = tmp_path / "vectors" / "chunks.lance"
    broken.mkdir(parents=True)
    (broken / "broken").write_bytes(b"unreadable table")
    before = fingerprint(tmp_path)
    report = diagnose.storage(tmp_path)["vector_tables"][0]
    assert report["status"] == "unavailable"
    assert "unreadable" not in json.dumps(report)
    assert fingerprint(tmp_path) == before


def test_refuse_symlink_without_touching_target(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret").write_text("not in report")
    (root / "linked").symlink_to(outside, target_is_directory=True)
    before = fingerprint(outside)
    with pytest.raises(ValueError, match="symlinks"):
        diagnose.storage(root)
    assert fingerprint(outside) == before


def test_unknown_version_metadata_is_not_zero(tmp_path, monkeypatch):
    table = lancedb.connect(tmp_path / "vectors").create_table("chunks", data=rows(0, 3))
    lancedb.connect(tmp_path / "vectors").create_table("empty", schema=table.schema)
    empty = next(t for t in diagnose.storage(tmp_path)["vector_tables"] if t["table"] == "empty")
    assert (empty["rows"], empty["data_files"], empty["deleted_data_bytes_estimate"],
            empty["reclaimable_bytes_estimate"]) == (0, 0, 0, 0)
    original = type(table).list_versions
    monkeypatch.setattr(type(table), "list_versions",
                        lambda self: [{**v, "metadata": {}} for v in original(self)])
    before = fingerprint(tmp_path)
    report = diagnose.storage(tmp_path)["vector_tables"][0]
    assert report["status"] == "ok"
    assert report["rows"] == 3
    for field in ("latest_referenced_data_bytes", "deleted_rows", "deleted_data_bytes_estimate",
                  "historical_or_unreferenced_data_bytes_estimate", "reclaimable_bytes_estimate",
                  "retained_bytes_estimate"):
        assert report[field] is None
    assert fingerprint(tmp_path) == before
