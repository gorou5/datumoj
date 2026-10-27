"""Offline Eltiro commands. Operator errors never include submitted file content."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from pathlib import Path
from typing import Annotated

import typer
import yaml

from datumoj.eltiro.intake import IntakeService, load_consumers, open_directory
from datumoj.eltiro.match_store import SQLiteMatchStore
from datumoj.eltiro.models import SeriesIdentity
from datumoj.eltiro.registry_store import SQLiteDemandRegistry
from datumoj.rikolto.core.contracts import SourceSpec

app = typer.Typer(no_args_is_help=True, help="Eltiro local demand intake.")
demands_app = typer.Typer(no_args_is_help=True)
app.add_typer(demands_app, name="demands")
matches_app = typer.Typer(no_args_is_help=True)
app.add_typer(matches_app, name="matches")


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
