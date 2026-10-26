"""Local synchronous storage contracts, independent of matching and execution.

No network, authentication, timeouts or Rikolto writes. Backends propagate storage errors;
validation/conflicting writes raise ValueError, updating a missing key raises KeyError.
Returned values must be detached copies: callers cannot mutate persisted state.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from datumoj.eltiro.models import Confirmation, DemandSpec, MatchRecord, SeriesIdentity, SeriesProfile


@runtime_checkable
class DemandRegistry(Protocol):
    def validate(self, path_or_bytes: str | Path | bytes) -> DemandSpec:
        """Same bounded validation as load_demand; does not register anything."""
        ...

    def register(self, demand: DemandSpec, *, active: bool = True) -> None:
        """Immutable (id, version): identical re-registration is idempotent; changes raise ValueError.

        active=True selects this version as the sole active version of its id. False leaves
        the active selection unchanged. Registration revalidates even constructed model instances.
        """
        ...

    def get(self, demand_id: str, version: int) -> DemandSpec | None:
        ...

    def set_active(self, demand_id: str, version: int, active: bool) -> None:
        """Activate this version (deactivating its predecessor), or deactivate only this version."""
        ...

    def list_active(self) -> list[DemandSpec]:
        """Sorted by demand id; at most one version per id."""
        ...


@runtime_checkable
class ProfileStore(Protocol):
    def write_generation(self, generation: int, profiles: Sequence[SeriesProfile]) -> None:
        """Atomically replace one generation, retaining all others; empty clears this generation.

        Reject mismatched generations and duplicate complete SeriesIdentity values before any write.
        Revalidate all models. Series identity includes *all* fixed dimensions.
        """
        ...

    def get(self, generation: int, series: SeriesIdentity) -> SeriesProfile | None:
        ...

    def list(self, generation: int) -> list[SeriesProfile]:
        """Insertion order within this generation; [] for a missing generation."""
        ...


@runtime_checkable
class MatchStore(Protocol):
    def put(self, record: MatchRecord) -> None:
        """Upsert by (demand_id, demand_version, indicator_key, series, generation).

        Revalidate; updating another property preserves insertion order. Human confirmation is
        retained on upsert; only set_confirmation may change it. Different generations coexist.
        """
        ...

    def for_demand(self, demand_id: str, version: int, generation: int | None = None) -> list[MatchRecord]:
        """Insertion order, optionally restricted to a generation; [] for a missing demand."""
        ...

    def set_confirmation(
        self, demand_id: str, version: int, indicator_key: str, series: SeriesIdentity,
        generation: int, confirmation: Confirmation,
    ) -> None:
        """Rejection also clears auto_bind; invalid status raises ValueError without writing."""
        ...
