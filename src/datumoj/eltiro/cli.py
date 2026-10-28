"""Offline Eltiro commands. Operator errors never include submitted file content."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated

import typer
import yaml
from pydantic import TypeAdapter
from typer._click.exceptions import UsageError
from typer.core import TyperCommand, TyperGroup

from datumoj.eltiro.alerts import Alert, SQLiteAlertStore, ack_alert, list_alerts
from datumoj.eltiro.concept_notes import ConceptNote, list_concept_notes
from datumoj.eltiro.drafts import draft_for_case, list_drafts
from datumoj.eltiro.executor import run_registered
from datumoj.eltiro.gaps import aggregate_gaps, gap_report
from datumoj.eltiro.intake import IntakeService, load_consumers, open_directory
from datumoj.eltiro.license_gate import LicenseGateError
from datumoj.eltiro.match_store import SQLiteMatchStore
from datumoj.eltiro.models import SeriesIdentity
from datumoj.eltiro.package import package_run
from datumoj.eltiro.page_cases import MAX_PAGE_BYTES, parse_cases, record_cases
from datumoj.eltiro.profile_store import SQLiteProfileStore
from datumoj.eltiro.registry_store import SQLiteDemandRegistry
from datumoj.eltiro.rematch import refresh
from datumoj.eltiro.runs import RunStore
from datumoj.rikolto.core.contracts import SourceSpec
from datumoj.rikolto.core.models import GenerationToken
from datumoj.rikolto.wiring import open_generation_views


class _FixedErrorCommand(TyperCommand):
    def parse_args(self, ctx, args):
        try:
            return super().parse_args(ctx, args)
        except UsageError:
            _error()


class _FixedErrorGroup(TyperGroup):
    def parse_args(self, ctx, args):
        try:
            return super().parse_args(ctx, args)
        except UsageError:
            _error()

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except UsageError:
            _error()

app = typer.Typer(no_args_is_help=True, help="Eltiro local demand intake.")
demands_app = typer.Typer(no_args_is_help=True)
app.add_typer(demands_app, name="demands")
matches_app = typer.Typer(no_args_is_help=True)
app.add_typer(matches_app, name="matches")
runs_app = typer.Typer(no_args_is_help=True)
app.add_typer(runs_app, name="runs")
alerts_app = typer.Typer(no_args_is_help=True, cls=_FixedErrorGroup)
app.add_typer(alerts_app, name="alerts")
gaps_app = typer.Typer(no_args_is_help=True, cls=_FixedErrorGroup)
app.add_typer(gaps_app, name="gaps")
notes_app = typer.Typer(no_args_is_help=True, cls=_FixedErrorGroup)
app.add_typer(notes_app, name="notes")
pages_app = typer.Typer(no_args_is_help=True, cls=_FixedErrorGroup)
app.add_typer(pages_app, name="pages")
drafts_app = typer.Typer(no_args_is_help=True, cls=_FixedErrorGroup)
app.add_typer(drafts_app, name="drafts")


class SourceDirectory:
    """Read the host's authoritative YAML records through the SourceSpec contract.

    No implementation dependency on Rikolto stores/wiring; no network or writes.
    The operator must supply the validated source registry, never an inbox directory.
    """

    def __init__(self, directory: Path):
        if not directory.is_dir():
            raise ValueError("source registry unavailable")
        self.specs = {}
        for path in sorted(directory.glob("*.yaml")):
            spec = SourceSpec.model_validate(yaml.safe_load(path.read_bytes()))
            if spec.id in self.specs or path.stem != spec.id:
                raise ValueError("invalid source registry")
            self.specs[spec.id] = spec

    def get(self, source_id: str) -> SourceSpec | None:
        return self.specs.get(source_id)

    def all(self) -> list[SourceSpec]:
        return [self.specs[key] for key in sorted(self.specs)]


@app.callback()
def main(
    ctx: typer.Context,
    data_dir: Annotated[Path, typer.Option("--data-dir", envvar="DATUMOJ_DATA_DIR")] = Path("data"),
    sources_dir: Annotated[Path, typer.Option("--sources-dir")] = Path("sources"),
) -> None:
    ctx.obj = {"data_dir": data_dir.resolve(), "sources_dir": sources_dir}


def _state_path(ctx: typer.Context, filename: str) -> Path:
    root = ctx.obj["data_dir"]
    load_consumers(root)  # Validate containment/overlap before opening mutable state.
    descriptor = open_directory(root, root / "eltiro")
    try:
        # SQLite opens its own path; internal state must be host-owned, not writable
        # by inbox publishers. Reject pre-existing non-regular database/journal files.
        for name in (filename, *(filename + suffix for suffix in ("-journal", "-wal", "-shm"))):
            try:
                info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("invalid registry state file")
    finally:
        os.close(descriptor)
    return root / "eltiro" / filename


def _registry(ctx: typer.Context) -> SQLiteDemandRegistry:
    return SQLiteDemandRegistry(_state_path(ctx, "demands.sqlite3"))


def _matches(ctx: typer.Context) -> SQLiteMatchStore:
    _state_path(ctx, "matches.sqlite")
    return SQLiteMatchStore(ctx.obj["data_dir"])


def _error() -> None:
    typer.echo("Eltiro operation failed; check configuration and local state.", err=True)
    raise typer.Exit(1)


@app.command("intake")
def intake_command(ctx: typer.Context) -> None:
    try:
        with _registry(ctx) as registry:
            service = IntakeService(ctx.obj["data_dir"], registry, SourceDirectory(ctx.obj["sources_dir"]))
            typer.echo(json.dumps(service.scan()))
    except (ValueError, OSError, sqlite3.Error, yaml.YAMLError):
        _error()


@app.command()
def submit(
    ctx: typer.Context,
    consumer: Annotated[str, typer.Option("--consumer")],
    file: Annotated[Path, typer.Argument()],
) -> None:
    try:
        with _registry(ctx) as registry:
            service = IntakeService(ctx.obj["data_dir"], registry, SourceDirectory(ctx.obj["sources_dir"]))
            receipt = service.submit(consumer, file)
            typer.echo(json.dumps(receipt))
    except (ValueError, OSError, sqlite3.Error, yaml.YAMLError):
        _error()
    if receipt["conclusion"] == "rejected":
        raise typer.Exit(1)


@demands_app.command("list")
def list_demands(ctx: typer.Context) -> None:
    try:
        with _registry(ctx) as registry:
            typer.echo(json.dumps(registry.list_records()))
    except (ValueError, OSError, sqlite3.Error):
        _error()


@demands_app.command("show")
def show_demand(ctx: typer.Context, demand_id: str, version: Annotated[int | None, typer.Option("--version")] = None):
    try:
        with _registry(ctx) as registry:
            if version is None:
                versions = [row["version"] for row in registry.list_records() if row["id"] == demand_id]
                version = max(versions) if versions else None
            demand = registry.get(demand_id, version) if version is not None else None
            if demand is None:
                raise ValueError("unknown demand")
            typer.echo(demand.model_dump_json(by_alias=True))
    except (ValueError, OSError, sqlite3.Error):
        _error()


@demands_app.command("deactivate")
def deactivate(ctx: typer.Context, demand_id: str, version: Annotated[int, typer.Option("--version")]):
    try:
        with _registry(ctx) as registry:
            registry.set_active(demand_id, version, False)
            typer.echo(json.dumps({"id": demand_id, "version": version, "active": False}))
    except (ValueError, KeyError, OSError, sqlite3.Error):
        _error()


@matches_app.command("queue")
def match_queue(
    ctx: typer.Context,
    consumer: Annotated[str | None, typer.Option("--consumer")] = None,
    demand: Annotated[str | None, typer.Option("--demand")] = None,
) -> None:
    try:
        with _registry(ctx) as registry, _matches(ctx) as store:
            records = []
            for row in registry.list_records():
                if (consumer is None or row["consumer"] == consumer) and (demand is None or row["id"] == demand):
                    records.extend(r.model_dump(mode="json") for r in store.for_demand(row["id"], row["version"])
                                   if r.confirmation == "pending" and not r.auto_bind)
            typer.echo(json.dumps(records, ensure_ascii=False))
    except (ValueError, OSError, sqlite3.Error):
        _error()


def _confirm_match(ctx, demand_id, version, indicator, series, generation, confirmation):
    try:
        identity = SeriesIdentity.model_validate_json(series)
        if version < 1 or generation < 1:
            raise ValueError("invalid version or generation")
        with _registry(ctx) as registry, _matches(ctx) as store:
            if registry.get(demand_id, version) is None:
                raise KeyError("unknown demand")
            store.set_confirmation(demand_id, version, indicator, identity, generation, confirmation)
            typer.echo(json.dumps({"confirmation": confirmation}))
    except (ValueError, KeyError, OSError, sqlite3.Error):
        _error()


@matches_app.command("confirm")
def confirm_match(
    ctx: typer.Context, demand_id: str,
    version: Annotated[int, typer.Option("--version")],
    indicator: Annotated[str, typer.Option("--indicator")],
    series: Annotated[str, typer.Option("--series")],
    generation: Annotated[int, typer.Option("--generation")],
) -> None:
    _confirm_match(ctx, demand_id, version, indicator, series, generation, "confirmed")


@matches_app.command("reject")
def reject_match(
    ctx: typer.Context, demand_id: str,
    version: Annotated[int, typer.Option("--version")],
    indicator: Annotated[str, typer.Option("--indicator")],
    series: Annotated[str, typer.Option("--series")],
    generation: Annotated[int, typer.Option("--generation")],
) -> None:
    _confirm_match(ctx, demand_id, version, indicator, series, generation, "rejected")


@app.command("run")
def run_demand(
    ctx: typer.Context, demand_id: str,
    version: Annotated[int | None, typer.Option("--version")] = None,
    generation_token: Annotated[str | None, typer.Option("--generation-token")] = None,
):
    try:
        _state_path(ctx, "profiles.sqlite")
        _state_path(ctx, "matches.sqlite")
        with _registry(ctx) as demands:
            record = run_registered(demand_id, version=version, generation_token=generation_token,
                                    data_root=ctx.obj["data_dir"], registry=SourceDirectory(ctx.obj["sources_dir"]),
                                    demands=demands)
        typer.echo(json.dumps({k: record[k] for k in ("run_id", "row_count", "gap_count")}))
    except Exception:
        _error()


@runs_app.command("list")
def list_runs(ctx: typer.Context):
    try:
        load_consumers(ctx.obj["data_dir"])
        typer.echo(json.dumps(RunStore(ctx.obj["data_dir"]).list()))
    except Exception:
        _error()


@runs_app.command("show")
def show_run(ctx: typer.Context, run_id: str):
    try:
        load_consumers(ctx.obj["data_dir"])
        typer.echo(json.dumps(RunStore(ctx.obj["data_dir"]).show(run_id)))
    except Exception:
        _error()


@app.command("package")
def package_command(ctx: typer.Context, run_id: str):
    try:
        consumers = load_consumers(ctx.obj["data_dir"])
        with _registry(ctx) as demands:
            result = package_run(run_id, data_root=ctx.obj["data_dir"],
                                 registry=SourceDirectory(ctx.obj["sources_dir"]),
                                 consumers=consumers, demands=demands)
        typer.echo(json.dumps({**result, "path": str(result["path"])}))
    except LicenseGateError as exc:
        typer.echo(json.dumps({"code": exc.code, "sources": exc.sources}), err=True)
        raise typer.Exit(1)
    except Exception:
        typer.echo(json.dumps({"code": "package_failed", "sources": []}), err=True)
        raise typer.Exit(1)


@app.command("refresh", cls=_FixedErrorCommand)
def refresh_command(
    ctx: typer.Context,
    previous: Annotated[int, typer.Option("--previous")],
    as_of: Annotated[str, typer.Option("--as-of")],
    generation_token: Annotated[str | None, typer.Option("--generation-token")] = None,
):
    try:
        day = date.fromisoformat(as_of)
        if day.isoformat() != as_of:
            raise ValueError("expected ISO date")
        root = ctx.obj["data_dir"]
        consumers = {c.id: c for c in load_consumers(root).consumers}
        for filename in ("profiles.sqlite", "matches.sqlite", "alerts.sqlite"):
            _state_path(ctx, filename)
        with open_generation_views(root, generation_token) as views:
            if generation_token is not None and views.generation_token != generation_token:
                raise ValueError("generation token mismatch")
            generation = GenerationToken.parse(views.generation_token).generation
            with (_registry(ctx) as demands, SQLiteProfileStore(root) as profiles,
                  _matches(ctx) as matches, SQLiteAlertStore(root) as alerts):
                counts = refresh(
                    generation, previous, registry=demands, profiles=profiles, consumers=consumers,
                    store=matches, alerts=alerts, observations=views.observations,
                    catalog=views.catalog, chunks=views.chunks, as_of=day, vector=False, model=False,
                )
        typer.echo(json.dumps(asdict(counts)))
    except Exception:  # noqa: BLE001 -- Redact every operation error at the CLI boundary.
        _error()


@alerts_app.command("list")
def alerts_list(
    ctx: typer.Context,
    consumer: Annotated[str | None, typer.Option("--consumer")] = None,
    demand: Annotated[str | None, typer.Option("--demand")] = None,
    all_alerts: Annotated[bool, typer.Option("--all")] = False,
):
    try:
        _state_path(ctx, "alerts.sqlite")
        alerts = list_alerts(ctx.obj["data_dir"], consumer=consumer, demand=demand, include_acked=all_alerts)
        typer.echo(TypeAdapter(list[Alert]).dump_json(alerts).decode())
    except Exception:  # noqa: BLE001 -- Redact every operation error at the CLI boundary.
        _error()


@alerts_app.command("ack")
def alerts_ack(ctx: typer.Context, alert_id: str):
    try:
        _state_path(ctx, "alerts.sqlite")
        ack_alert(alert_id, data_root=ctx.obj["data_dir"])
        typer.echo(json.dumps({"acked": True}))
    except Exception:  # noqa: BLE001 -- Redact every operation error at the CLI boundary.
        _error()


@gaps_app.command("show")
def gaps_show(
    ctx: typer.Context, demand_id: str,
    generation: Annotated[int, typer.Option("--generation")],
    version: Annotated[int | None, typer.Option("--version")] = None,
):
    try:
        root = ctx.obj["data_dir"]
        consumers = {c.id: c for c in load_consumers(root).consumers}
        _state_path(ctx, "profiles.sqlite")
        with (_registry(ctx) as demands, _matches(ctx) as matches,
              SQLiteProfileStore(root) as profiles):
            if version is None:
                versions = [r["version"] for r in demands.list_records() if r["id"] == demand_id]
                version = max(versions) if versions else None
            demand = demands.get(demand_id, version) if version is not None else None
            if demand is None:
                raise ValueError("unknown demand")
            report = gap_report(demand, generation, consumer=consumers[demand.consumer],
                                matches=matches, profiles=profiles, registry=SourceDirectory(ctx.obj["sources_dir"]),
                                data_dir=root)
        typer.echo(json.dumps({
            "path": str(root / "eltiro" / "gaps" / f"{demand.id}-v{demand.version}-g{generation}.json"),
            "indicator_count": len(report["indicators"]),
            "gap_count": sum(len(i["gaps"]) for i in report["indicators"]),
        }))
    except Exception:  # noqa: BLE001 -- Redact every operation error at the CLI boundary.
        _error()


@gaps_app.command("report")
def gaps_report(ctx: typer.Context, generation: Annotated[int, typer.Option("--generation")]):
    try:
        root = ctx.obj["data_dir"]
        consumers = {c.id: c for c in load_consumers(root).consumers}
        _state_path(ctx, "profiles.sqlite")
        with (_registry(ctx) as demands, _matches(ctx) as matches,
              SQLiteProfileStore(root) as profiles):
            aggregate_gaps(generation, demands=demands, consumers=consumers, matches=matches,
                           profiles=profiles, registry=SourceDirectory(ctx.obj["sources_dir"]), data_dir=root)
        typer.echo(json.dumps({"path": str(root / "eltiro" / "reports" / f"gaps-g{generation}.json"),
                               "markdown_path": str(root / "eltiro" / "reports" / f"gaps-g{generation}.md")}))
    except Exception:  # noqa: BLE001 -- Redact every operation error at the CLI boundary.
        _error()


@notes_app.command("list")
def notes_list(ctx: typer.Context, generation: Annotated[int, typer.Option("--generation")]):
    try:
        _state_path(ctx, "concept_notes.sqlite")
        notes = list_concept_notes(generation, data_root=ctx.obj["data_dir"])
        typer.echo(TypeAdapter(list[ConceptNote]).dump_json(notes).decode())
    except Exception:  # noqa: BLE001 -- Redact every operation error at the CLI boundary.
        _error()


def _read_cases(file: Path, kind: str):
    # Bound reads before parsing, and reject devices/FIFOs and symlinks.
    descriptor = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_PAGE_BYTES:
            raise ValueError("invalid page file")
        return parse_cases(stream.read(MAX_PAGE_BYTES + 1), kind)


@pages_app.command("record")
def pages_record(ctx: typer.Context, kind: str, file: Path):
    try:
        _state_path(ctx, "page_cases.sqlite")
        changes = record_cases(_read_cases(file, kind), data_dir=ctx.obj["data_dir"],
                               seen_at=datetime.now(UTC))
        typer.echo(json.dumps({key: len(getattr(changes, key))
                               for key in ("added", "changed", "disappeared", "unchanged", "failures")}))
    except Exception:  # noqa: BLE001 -- Redact every operation error at the CLI boundary.
        _error()


@drafts_app.command("make")
def drafts_make(
    ctx: typer.Context, kind: str, file: Path, case_no: str,
    consumer: Annotated[str, typer.Option("--consumer")],
):
    try:
        consumers = {c.id: c for c in load_consumers(ctx.obj["data_dir"]).consumers}
        if consumer not in consumers:
            raise ValueError("unknown consumer")
        parsed = _read_cases(file, kind)
        case = next((c for c in parsed.cases if c.number == case_no), None)
        if case is None or any(f.number == case_no for f in parsed.failures):
            raise ValueError("case unavailable")
        path = draft_for_case(case, consumer=consumer, registry=SourceDirectory(ctx.obj["sources_dir"]),
                              data_dir=ctx.obj["data_dir"], router=None)
        typer.echo(json.dumps({"path": str(path), "registered": False,
                               "next": "Edit the draft, then use eltiro submit to submit the edited file."}))
    except Exception:  # noqa: BLE001 -- Redact every operation error at the CLI boundary.
        _error()


@drafts_app.command("list")
def drafts_list(ctx: typer.Context):
    try:
        load_consumers(ctx.obj["data_dir"])
        typer.echo(json.dumps({"paths": [str(path) for path in list_drafts(ctx.obj["data_dir"])],
                               "registered": False,
                               "next": "Edit the draft, then use eltiro submit to submit the edited file."}))
    except Exception:  # noqa: BLE001 -- Redact every operation error at the CLI boundary.
        _error()
