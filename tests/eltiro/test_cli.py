"""The CLI uses only local configuration, a synthetic registry and local inputs."""

import json

import pytest
from typer.testing import CliRunner

from datumoj.eltiro.cli import app
from datumoj.eltiro.match_store import SQLiteMatchStore
from datumoj.eltiro.models import MAX_FILE_BYTES
from datumoj.eltiro.registry_store import SQLiteDemandRegistry
from datumoj.eltiro.testing.samples import make_demand, make_match, make_series
from datumoj.rikolto.testing.samples import make_spec


@pytest.fixture
def cli(tmp_path):
    state = tmp_path / "eltiro"
    state.mkdir()
    config = {"consumers": [{
        "id": "example-consumer", "inbox": "consumer/inbox", "outbox": "consumer/outbox",
        "allowed_sources": ["intl-wdi"], "models": {"consumer": "example-consumer",
        "authorized_sources": ["intl-wdi"]}, "max_active_demands": 1,
    }]}
    (state / "consumers.yaml").write_text(json.dumps(config))
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "intl-wdi.yaml").write_text(make_spec("intl-wdi").model_dump_json())
    runner = CliRunner()

    def invoke(*args):
        return runner.invoke(app, ["--data-dir", str(tmp_path), "--sources-dir", str(sources), *args])

    return tmp_path, invoke


def test_submit_list_show_deactivate_and_intake_persist_across_invocations(cli):
    root, invoke = cli
    file = root / "request.yaml"
    file.write_text(make_demand().model_dump_json(by_alias=True))
    result = invoke("submit", "--consumer", "example-consumer", str(file))
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["conclusion"] == "accepted"
    assert file.exists()
    result = invoke("demands", "list")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["active"] == 1
    for args in (("demands", "show", "example-demand"), ("demands", "show", "example-demand", "--version", "1")):
        result = invoke(*args)
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["purpose"] == make_demand().purpose
    result = invoke("demands", "deactivate", "example-demand", "--version", "1")
    assert result.exit_code == 0, result.output
    assert json.loads(invoke("demands", "list").output)[0]["active"] == 0
    assert json.loads(invoke("intake").output) == {"accepted": 0, "rejected": 0}
    (root / "consumer/inbox/next.yaml").write_text(make_demand(version=2).model_dump_json(by_alias=True))
    result = invoke("intake")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"accepted": 1, "rejected": 0}
    assert json.loads(invoke("demands", "show", "example-demand").output)["version"] == 2


@pytest.mark.parametrize("kind", ["invalid", "oversized", "symlink", "consumer"])
def test_submit_rejections_have_receipts_and_audit_without_content(cli, kind):
    root, invoke = cli
    file = root / "request.yaml"
    if kind == "oversized":
        file.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    elif kind == "symlink":
        target = root / "target.yaml"
        target.write_text("PRIVATE CONTENT")
        file.symlink_to(target)
    elif kind == "consumer":
        file.write_text(make_demand(consumer="foreign-consumer", purpose="PRIVATE CONTENT").model_dump_json())
    else:
        file.write_text('{"unknown": "PRIVATE CONTENT"}')
    result = invoke("submit", "--consumer", "example-consumer", str(file))
    assert result.exit_code == 1, result.output
    assert "PRIVATE CONTENT" not in result.output
    assert "foreign-consumer" not in result.output
    assert json.loads(result.output)["conclusion"] == "rejected"
    assert len(list((root / "consumer/outbox").glob("*.json"))) == 1
    assert len((root / "eltiro/audit.jsonl").read_text().splitlines()) == 1
    assert json.loads(invoke("demands", "list").output) == []


def test_configuration_and_lookup_errors_are_fixed_messages(cli):
    root, invoke = cli
    for args in (("demands", "show", "missing"), ("demands", "deactivate", "missing", "--version", "1"),
                 ("submit", "--consumer", "missing", str(root / "missing.yaml"))):
        result = invoke(*args)
        assert result.exit_code == 1
        assert "Eltiro operation failed" in result.output
    (root / "eltiro/consumers.yaml").write_text('{"password": "PRIVATE CONFIG CONTENT"}')
    result = invoke("intake")
    assert result.exit_code == 1
    assert "PRIVATE" not in result.output and "password" not in result.output


def test_sqlite_state_symlink_is_rejected(cli):
    root, invoke = cli
    sentinel = root / "sentinel"
    sentinel.write_text("unchanged")
    (root / "eltiro/demands.sqlite3").symlink_to(sentinel)
    result = invoke("intake")
    assert result.exit_code == 1
    assert sentinel.read_text() == "unchanged"


def test_match_queue_filters_and_confirm_reject_persist(cli):
    root, invoke = cli
    with SQLiteDemandRegistry(root / "eltiro/demands.sqlite3") as registry:
        registry.register(make_demand())
        registry.register(make_demand(id="second-demand", consumer="second-consumer"))
    record = make_match()
    with SQLiteMatchStore(root) as store:
        store.put(record)
        store.put(make_match(demand_id="second-demand"))
        store.put(make_match(series=make_series(table_key="auto"), auto_bind=True,
                             compatibility={"unit": "same", "methodology": "same", "survey": "same"}))
    assert len(json.loads(invoke("matches", "queue").output)) == 2
    for filters in (("--consumer", "example-consumer"), ("--demand", "example-demand")):
        queued = invoke("matches", "queue", *filters)
        assert queued.exit_code == 0, queued.output
        assert [r["demand_id"] for r in json.loads(queued.output)] == ["example-demand"]
    args = ("example-demand", "--version", "1", "--indicator", "gdp", "--series", record.series.model_dump_json(),
            "--generation", "1")
    for command, status in (("confirm", "confirmed"), ("reject", "rejected")):
        result = invoke("matches", command, *args)
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == {"confirmation": status}
        assert json.loads(invoke("matches", "queue", "--demand", "example-demand").output) == []
        with SQLiteMatchStore(root) as store:
            assert store.for_demand("example-demand", 1)[0].confirmation == status


def test_match_cli_errors_redact_input_and_refuse_symlink(cli):
    root, invoke = cli
    result = invoke("matches", "confirm", "example-demand", "--version", "1", "--indicator", "gdp",
                    "--series", '{"PRIVATE CONTENT":1}', "--generation", "1")
    assert result.exit_code == 1 and "PRIVATE CONTENT" not in result.output
    assert "Eltiro operation failed" in result.output
    with SQLiteDemandRegistry(root / "eltiro/demands.sqlite3") as registry:
        registry.register(make_demand())
    result = invoke("matches", "reject", "example-demand", "--version", "1", "--indicator", "gdp",
                    "--series", make_series().model_dump_json(), "--generation", "1")
    assert result.exit_code == 1 and "Eltiro operation failed" in result.output
    (root / "eltiro/matches.sqlite").unlink()
    sentinel = root / "sentinel"
    sentinel.write_text("unchanged")
    (root / "eltiro/matches.sqlite").symlink_to(sentinel)
    result = invoke("matches", "queue")
    assert result.exit_code == 1 and sentinel.read_text() == "unchanged"


def test_run_commands_select_registered_version_and_show_private_records(cli):
    from tests.eltiro.test_executor import observation, publish, seed

    root, invoke = cli
    store, _ = seed(root, [[observation()]])
    token = publish(store)
    with SQLiteDemandRegistry(root / "eltiro/demands.sqlite3") as demands:
        demands.register(make_demand(indicators=[{"key": "gdp", "concept": "NY.GDP.MKTP.CD", "unit": "USD"}]))
        demands.register(make_demand(version=2, indicators=[{"key": "gdp", "concept": "NY.GDP.MKTP.CD", "unit": "USD"}]))
    first = invoke("run", "example-demand", "--version", "1", "--generation-token", token)
    assert first.exit_code == 0, first.output
    receipt = json.loads(first.output)
    assert receipt["row_count"] == 1 and receipt["gap_count"] == 0
    shown = invoke("runs", "show", receipt["run_id"])
    assert shown.exit_code == 0 and json.loads(shown.output)["demand_version"] == 1
    second = invoke("run", "example-demand")
    assert second.exit_code == 0, second.output
    listing = invoke("runs", "list")
    assert listing.exit_code == 0
    assert {r["demand_version"] for r in json.loads(listing.output)} == {1, 2}
    assert not (root / "consumer/outbox").exists()
    for args in (("run", "example-demand", "--generation-token", "PRIVATE TOKEN"),
                 ("run", "missing"), ("runs", "show", "../PRIVATE")):
        result = invoke(*args)
        assert result.exit_code == 1 and "PRIVATE" not in result.output
        assert "Eltiro operation failed" in result.output
    assert len(json.loads(invoke("runs", "list").output)) == 2


def test_package_command_success_and_safe_license_denial(cli):
    from tests.eltiro.test_package import MARKER, sources, write_run
    root, invoke = cli
    demand = make_demand(accept_outflow=["raw_ok", "derived_only"])
    with SQLiteDemandRegistry(root / "eltiro/demands.sqlite3") as demands:
        demands.register(demand)
    config_file = root / "eltiro/consumers.yaml"
    config = json.loads(config_file.read_bytes())
    config["consumers"][0]["accept_outflow"] = ["raw_ok", "derived_only"]
    config_file.write_text(json.dumps(config))
    source_file = root / "sources/intl-wdi.yaml"
    source_file.write_text(sources({"intl-wdi": "derived_only"}).get("intl-wdi").model_dump_json())
    record = write_run(root)
    result = invoke("package", record["run_id"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["row_count"] == 1
    assert MARKER not in result.output
    source_file.write_text(sources({"intl-wdi": "no_outflow"}).get("intl-wdi").model_dump_json())
    result = invoke("package", record["run_id"])
    assert result.exit_code == 1
    assert json.loads(result.output) == {"code": "no_outflow", "sources": ["intl-wdi"]}
    assert "123456789" not in result.output and MARKER not in result.output
    result = invoke("package", "PRIVATE CONTENT")
    assert result.exit_code == 1
    assert json.loads(result.output) == {"code": "package_failed", "sources": []}


@pytest.mark.parametrize("args", [
    ("refresh", "--previous", "PRIVATE 987654321", "--as-of", "2026-10-28"),
    ("refresh", "--previous", "1"),
    ("refresh", "--previous", "1", "--as-of", "2026-10-28", "--model", "PRIVATE"),
    ("gaps", "show", "example-demand", "--generation", "PRIVATE 987654321"),
    ("notes", "list"),
    ("pages", "record", "verify"),
    ("drafts", "make", "verify", "PRIVATE", "V07"),
    ("alerts", "ack"),
    ("alerts", "--unknown", "PRIVATE 987654321"),
    ("notes", "generate", "PRIVATE 987654321"),
])
def test_new_command_argument_errors_are_fixed(cli, args):
    _, invoke = cli
    result = invoke(*args)
    assert result.exit_code == 1
    assert result.output == "Eltiro operation failed; check configuration and local state.\n"


@pytest.mark.parametrize("kind", ["oversized", "symlink", "directory", "fifo", "bad-kind"])
def test_local_page_input_boundaries(cli, kind):
    import os

    from datumoj.eltiro.page_cases import MAX_PAGE_BYTES
    from tests.eltiro.test_page_cases import page

    root, invoke = cli
    file = root / "page.html"
    if kind == "oversized":
        file.write_bytes(b"x" * (MAX_PAGE_BYTES + 1))
    elif kind == "symlink":
        target = root / "target.html"
        target.write_text(page())
        file.symlink_to(target)
    elif kind == "directory":
        file.mkdir()
    elif kind == "fifo":
        os.mkfifo(file)
    else:
        file.write_text(page())
    result = invoke("pages", "record", "unknown" if kind == "bad-kind" else "verify", str(file))
    assert result.exit_code == 1
    assert result.output == "Eltiro operation failed; check configuration and local state.\n"
    assert not (root / "eltiro/page_cases.sqlite").exists()


@pytest.mark.parametrize("args,filename", [
    (("alerts", "list"), "alerts.sqlite"),
    (("notes", "list", "--generation", "1"), "concept_notes.sqlite"),
    (("gaps", "report", "--generation", "1"), "profiles.sqlite"),
    (("pages", "record", "verify", "missing.html"), "page_cases.sqlite"),
])
def test_new_commands_preserve_state_symlink_targets(cli, args, filename):
    root, invoke = cli
    target = root / "sentinel"
    target.write_text("unchanged")
    (root / "eltiro" / filename).symlink_to(target)
    result = invoke(*args)
    assert result.exit_code == 1
    assert result.output == "Eltiro operation failed; check configuration and local state.\n"
    assert target.read_text() == "unchanged"
