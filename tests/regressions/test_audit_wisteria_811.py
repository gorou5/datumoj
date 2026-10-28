"""Offline CLI integration: generation pinning, tenant filters and inert drafts."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest
import yaml

from datumoj.eltiro import cli as commands
from datumoj.eltiro.alerts import SQLiteAlertStore
from datumoj.eltiro.concept_notes import ConceptNote, list_concept_notes
from datumoj.eltiro.match_store import SQLiteMatchStore
from datumoj.eltiro.models import ModelCacheKey, ModelUse
from datumoj.eltiro.profile_store import SQLiteProfileStore
from datumoj.eltiro.registry_store import SQLiteDemandRegistry
from datumoj.eltiro.testing.samples import make_demand, make_profile, make_series
from datumoj.rikolto.core.models import GenerationToken
from tests.eltiro.test_cli import cli as cli  # noqa: PLC0414 -- Re-export the shared pytest fixture.
from tests.eltiro.test_executor import observation, publish, seed
from tests.eltiro.test_page_cases import page

ERROR = "Eltiro operation failed; check configuration and local state.\n"
PRIVATE = "PRIVATE DEMAND AND SOURCE TEXT"
NUMBER = "987654321.125"


def decoded(result):
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def register(root, **changes):
    demand = make_demand(purpose=PRIVATE + NUMBER, **changes)
    with SQLiteDemandRegistry(root / "eltiro/demands.sqlite3") as registry:
        registry.register(demand)
    return demand


@pytest.mark.parametrize("explicit_token", [False, True])
def test_refresh_pins_only_new_generation_and_calls_existing_interface(cli, monkeypatch, explicit_token):
    root, invoke = cli
    live, _ = seed(root, [[observation(NUMBER)]])
    token = publish(live, 2)
    # Publish a later generation: explicit tokens must still select generation 2.
    if explicit_token:
        publish(live, 3)
    register(root, indicators=[{"key": "gdp", "concept": "NY.GDP.MKTP.CD", "unit": "USD"}],
             run={"notify_on": ["new_candidate"]})
    with SQLiteProfileStore(root) as profiles:
        profiles.write_generation(1, [make_profile(unit="USD")])
        profiles.write_generation(2, [make_profile(generation=2, unit="USD")])
    real_open, real_refresh = commands.open_generation_views, commands.refresh
    opened, refreshed = [], []

    @contextmanager
    def tracked_open(data_root, selected):
        opened.append(selected)
        with real_open(data_root, selected) as views:
            assert views.generation_token == token
            yield views

    def tracked_refresh(new, previous, **kwargs):
        assert (new, previous) == (2, 1)
        assert kwargs["as_of"] == date(2026, 10, 28)
        assert kwargs["model"] is False and kwargs["vector"] is False
        refreshed.append(new)
        return real_refresh(new, previous, **kwargs)

    monkeypatch.setattr(commands, "open_generation_views", tracked_open)
    monkeypatch.setattr(commands, "refresh", tracked_refresh)
    args = ("--generation-token", token) if explicit_token else ()
    result = invoke("refresh", "--previous", "1", "--as-of", "2026-10-28", *args)
    assert decoded(result) == {"added": 0, "modified": 0, "deleted": 0,
                               "rematched": 1, "carried": 0, "alerts": 1}
    assert opened == [token if explicit_token else None] and refreshed == [2]
    assert PRIVATE not in result.output and NUMBER not in result.output
    with SQLiteMatchStore(root) as matches:
        assert matches.for_demand("example-demand", 1, 2)
    assert not (root / "consumer/outbox").exists()


def test_refresh_unavailable_explicit_token_never_falls_back(cli):
    root, invoke = cli
    live, _ = seed(root, [[observation()]])
    publish(live, 2)
    unavailable = str(GenerationToken(generation=1, manifest_sha256="ff" * 32))
    result = invoke("refresh", "--previous", "1", "--as-of", "2026-10-28", "--generation-token", unavailable)
    assert result.exit_code == 1 and result.output == ERROR


def test_alert_list_consumer_demand_all_and_ack(cli):
    root, invoke = cli
    demand = make_demand(run={"notify_on": ["new_candidate"]})
    with SQLiteAlertStore(root) as alerts:
        alerts._add(demand, "gdp", make_series(), "new_candidate", 2, ("auto_bind_available",))
        alerts._add(make_demand(id="other-demand", consumer="other-consumer", run=demand.run),
                    "gdp", make_series(), "new_candidate", 2, ("auto_bind_available",))
    assert len(decoded(invoke("alerts", "list"))) == 2
    filtered = decoded(invoke("alerts", "list", "--consumer", demand.consumer, "--demand", demand.id))
    assert len(filtered) == 1 and filtered[0]["consumer"] == demand.consumer
    assert "other-consumer" not in json.dumps(filtered) and "other-demand" not in json.dumps(filtered)
    assert decoded(invoke("alerts", "list", "--consumer", demand.consumer, "--demand", "other-demand")) == []
    assert decoded(invoke("alerts", "ack", filtered[0]["alert_id"])) == {"acked": True}
    assert decoded(invoke("alerts", "list", "--consumer", demand.consumer)) == []
    assert decoded(invoke("alerts", "list", "--consumer", demand.consumer, "--all"))[0]["acked"] is True
    with SQLiteAlertStore(root) as alerts:
        assert alerts.list_alerts(consumer=demand.consumer, include_acked=True)[0].acked


def forbid_rikolto(*args, **kwargs):
    raise AssertionError("local report/list commands must not open Rikolto")


def test_gap_show_latest_explicit_version_and_host_report_without_rikolto(cli, monkeypatch):
    root, invoke = cli
    register(root)
    register(root, version=2)
    register(root, id="other-demand", consumer="other-consumer")
    config_file = root / "eltiro/consumers.yaml"
    config = json.loads(config_file.read_text())
    other = {**config["consumers"][0], "id": "other-consumer", "inbox": "other/inbox", "outbox": "other/outbox",
             "models": {"consumer": "other-consumer", "authorized_sources": ["intl-wdi"]}}
    config["consumers"].append(other)
    config_file.write_text(json.dumps(config))
    monkeypatch.setattr(commands, "open_generation_views", forbid_rikolto)
    latest = decoded(invoke("gaps", "show", "example-demand", "--generation", "7"))
    assert latest["indicator_count"] == 1 and latest["gap_count"] == 2
    report = json.loads(Path(latest["path"]).read_text())
    assert report["demand_version"] == 2 and report["generation"] == 7
    assert "other-consumer" not in json.dumps(report) and "other-demand" not in json.dumps(report)
    first = decoded(invoke("gaps", "show", "example-demand", "--version", "1", "--generation", "7"))
    assert json.loads(Path(first["path"]).read_text())["demand_version"] == 1
    aggregate = decoded(invoke("gaps", "report", "--generation", "7"))
    assert json.loads(Path(aggregate["path"]).read_text())["active_demands"] == 2
    assert Path(aggregate["markdown_path"]).is_file()
    assert not (root / "consumer/outbox").exists() and not (root / "other/outbox").exists()


def test_notes_list_reads_stored_provenance_and_generation_only(cli, monkeypatch):
    root, invoke = cli
    assert list_concept_notes(1, data_root=root) == []  # Initialize the public store schema.
    key = ModelCacheKey(consumer="example-consumer", authorized_sources=["intl-wdi"],
                        generation_token=GenerationToken(generation=1, manifest_sha256="ab" * 32),
                        input_sha256="cd" * 32, provider="ollama", identity={"model": "example-local", "version": "v1"},
                        policy_sha256="ef" * 32, prompt_version="v1")
    note = ConceptNote(1, make_series(), "12" * 32, key, "Stored concept explanation.",
                       ModelUse(provider="ollama", model="example-local", version="v1", tier="low"))
    with sqlite3.connect(root / "eltiro/concept_notes.sqlite") as db:
        db.execute("INSERT INTO notes VALUES (?,?,?,?,?,?,?,?)", (
            note.generation, note.series.model_dump_json(), note.profile_sha256, key.digest,
            key.model_dump_json(), note.text, note.model_use.model_dump_json(), note.label))
    before = (root / "eltiro/concept_notes.sqlite").read_bytes()
    monkeypatch.setattr(commands, "open_generation_views", forbid_rikolto)
    notes = decoded(invoke("notes", "list", "--generation", "1"))
    assert len(notes) == 1 and notes[0]["text"] == note.text and notes[0]["label"] == "模型生成"
    assert notes[0]["model_use"] == note.model_use.model_dump(mode="json")
    assert notes[0]["cache_key"] == key.model_dump(mode="json")
    assert decoded(invoke("notes", "list", "--generation", "2")) == []
    assert (root / "eltiro/concept_notes.sqlite").read_bytes() == before


@pytest.mark.parametrize("kind,number", [("verify", "V07"), ("cases", "#007")])
def test_pages_record_changes_and_draft_commands_never_register(cli, kind, number):
    root, invoke = cli
    register(root)
    before = (root / "eltiro/demands.sqlite3").read_bytes()
    html = page(number=number, hypothesis=PRIVATE + NUMBER)
    if kind == "cases":
        html = html.replace('class="hyp"', 'class="q"').replace('class="method"', 'class="fuse"')
        html = html.replace('class="signal"', 'class="aha"').replace('</article>', '<span class="stag">category</span></article>')
    file = root / "page.html"
    file.write_text(html)
    args = ("pages", "record", kind, str(file))
    assert decoded(invoke(*args)) == {"added": 1, "changed": 0, "disappeared": 0, "unchanged": 0, "failures": 0}
    assert decoded(invoke(*args))["unchanged"] == 1
    file.write_text(html.replace(PRIVATE, PRIVATE + " changed"))
    assert decoded(invoke(*args))["changed"] == 1
    # The draft uses this current file, not the last recorded hash/content.
    file.write_text(html.replace(PRIVATE, PRIVATE + " current file"))
    draft = decoded(invoke("drafts", "make", kind, str(file), number, "--consumer", "example-consumer"))
    document = yaml.safe_load(Path(draft["path"]).read_text())
    assert document["draft"] and "current file" in document["purpose"]
    assert document["model_suggestions"] == {} and document["model_manual_reasons"] == []
    assert not draft["registered"] and "eltiro submit" in draft["next"]
    listing = decoded(invoke("drafts", "list"))
    assert listing["paths"] == [draft["path"]] and "eltiro submit" in listing["next"]
    replacement = "V08" if kind == "verify" else "#008"
    file.write_text(html.replace(number, replacement))
    changes = decoded(invoke(*args))
    assert changes["disappeared"] == 1 and changes["added"] == 1
    assert (root / "eltiro/demands.sqlite3").read_bytes() == before


@pytest.mark.parametrize("broken", ["absent", "invalid", "duplicate"])
def test_draft_case_missing_failed_or_duplicate_has_fixed_error(cli, broken):
    root, invoke = cli
    file = root / "page.html"
    html = page(hypothesis=PRIVATE + NUMBER)
    if broken == "invalid":
        html = html.replace('class="method"', 'class="unknown"')
    elif broken == "duplicate":
        html += html
    file.write_text(html)
    result = invoke("drafts", "make", "verify", str(file), "V99" if broken == "absent" else "V07",
                    "--consumer", "example-consumer")
    assert result.exit_code == 1 and result.output == ERROR
    assert not (root / "eltiro/demands.sqlite3").exists()
    assert not (root / "eltiro/drafts").exists()


@pytest.mark.parametrize("args", [
    ("refresh", "--previous", "1", "--as-of", PRIVATE + NUMBER),
    ("alerts", "list", "--consumer", PRIVATE + NUMBER),
    ("alerts", "ack", PRIVATE + NUMBER),
    ("gaps", "show", PRIVATE + NUMBER, "--generation", "1"),
    ("gaps", "report", "--generation", "0"),
    ("notes", "list", "--generation", "0"),
    ("pages", "record", "verify", PRIVATE + NUMBER),
    ("drafts", "make", "verify", PRIVATE + NUMBER, "V07", "--consumer", "example-consumer"),
    ("drafts", "list"),
])
def test_every_new_command_failure_redacts_data_paths_and_numbers(cli, args):
    root, invoke = cli
    # Trigger a real configuration error for commands whose normal empty result succeeds.
    if args[:2] in (("alerts", "list"), ("drafts", "list")):
        (root / "eltiro/consumers.yaml").write_text(PRIVATE + NUMBER)
    result = invoke(*args)
    assert result.exit_code == 1 and result.output == ERROR
    assert PRIVATE not in result.output and NUMBER not in result.output and str(root) not in result.output


def test_existing_empty_command_outputs_remain_exact(cli):
    _, invoke = cli
    assert invoke("intake").output == '{"accepted": 0, "rejected": 0}\n'
    for args in (("demands", "list"), ("matches", "queue"), ("runs", "list")):
        assert invoke(*args).output == "[]\n"
    assert invoke("demands", "show", "missing").output == ERROR
    assert invoke("package", "missing").output == '{"code": "package_failed", "sources": []}\n'
