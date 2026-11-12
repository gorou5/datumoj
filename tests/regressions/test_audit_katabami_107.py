"""No source text, URLs, keys or dynamically supplied labels escape accounting."""

import json
import sqlite3
from contextlib import closing

import pytest
from typer.testing import CliRunner

from datumoj.rikolto import cli, db_usage
from tests.test_cloud_usage_db import cloud_world as cloud_world  # noqa: PLC0414 -- Re-export pytest fixture.
from tests.test_db_usage import SECRET, URL, measure
from tests.test_db_usage import db_world as db_world  # noqa: PLC0414 -- Re-export pytest fixture.


def test_local_report_contains_only_fixed_labels_source_ids_and_numbers(db_world):
    text = json.dumps(measure(db_world), ensure_ascii=False)
    for forbidden in (SECRET, URL, "https://", "jp-test:recent", "snapshot-", "seed"):
        assert forbidden not in text
    def check(value):
        if isinstance(value, dict):
            for child in value.values():
                check(child)
        else:
            assert isinstance(value, int) or value in {"dbstat", "column_lengths"}
    check(measure(db_world))


def test_cloud_db_never_exposes_documents_or_storage_names(cloud_world):
    response = CliRunner().invoke(cli.app, ["cloud", "usage", "--db"])
    assert response.exit_code == 0, response.output
    for forbidden in (SECRET, URL, "https://", "PRIVATE", "jp-test:recent", "snapshot-"):
        assert forbidden not in response.output
    for key in cloud_world.before:
        assert key not in response.output
        if key.startswith("copies/"):
            assert key.rsplit("/", 1)[-1] not in response.output


def test_untrusted_source_id_is_rejected(db_world):
    with closing(sqlite3.connect(db_world.root / "chunks.sqlite")) as db, db:
        db.execute("UPDATE chunks SET source_id=?", (URL + SECRET,))
    with pytest.raises(ValueError):
        measure(db_world)


def test_untrusted_table_name_cannot_become_output_key(db_world):
    with closing(sqlite3.connect(db_world.root / "chunks.sqlite")) as db:
        db.execute(f'CREATE TABLE "{SECRET}" (value TEXT)')
    with pytest.raises(ValueError, match="unsupported_table"):
        db_usage.sqlite_usage(db_world.root / "chunks.sqlite")
