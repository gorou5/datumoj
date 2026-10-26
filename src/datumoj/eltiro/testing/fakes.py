"""Detached, revalidated in-memory implementations; never read Rikolto stores."""

from datumoj.eltiro.models import MatchRecord, SeriesIdentity, load_demand


def _copy(model):
    return type(model).model_validate(model.model_dump(by_alias=True))


def _series_key(series: SeriesIdentity) -> tuple:
    return (series.source_id, series.table_key, series.indicator, series.indicator_code,
            tuple(sorted(series.fixed_dimensions.items())))


def _match_key(record: MatchRecord) -> tuple:
    return (record.demand_id, record.demand_version, record.indicator_key, _series_key(record.series), record.generation)


class FakeDemandRegistry:
    def __init__(self):
        self._demands = {}
        self._active = {}

    def validate(self, path_or_bytes):
        return load_demand(path_or_bytes)

    def register(self, demand, *, active=True):
        demand = _copy(demand)
        key = (demand.id, demand.version)
        if key in self._demands and self._demands[key] != demand:
            raise ValueError("demand version is immutable")
        self._demands[key] = demand
        if active:
            self._active[demand.id] = demand.version

    def get(self, demand_id, version):
        value = self._demands.get((demand_id, version))
        return _copy(value) if value is not None else None

    def set_active(self, demand_id, version, active):
        if (demand_id, version) not in self._demands:
            raise KeyError((demand_id, version))
        if active:
            self._active[demand_id] = version
        elif self._active.get(demand_id) == version:
            del self._active[demand_id]

    def list_active(self):
        return [self.get(key, self._active[key]) for key in sorted(self._active)]


class FakeProfileStore:
    def __init__(self):
        self._generations = {}

    def write_generation(self, generation, profiles):
        # Validate the generation even for an empty replacement.
        from pydantic import TypeAdapter

        from datumoj.eltiro.models import Generation

        TypeAdapter(Generation).validate_python(generation)
        validated = [_copy(profile) for profile in profiles]
        values = {_series_key(profile.series): profile for profile in validated}
        if len(values) != len(validated) or any(profile.generation != generation for profile in validated):
            raise ValueError("duplicate series or mismatched generation")
        self._generations[generation] = values

    def get(self, generation, series):
        value = self._generations.get(generation, {}).get(_series_key(series))
        return _copy(value) if value is not None else None

    def list(self, generation):
        return [_copy(value) for value in self._generations.get(generation, {}).values()]


class FakeMatchStore:
    def __init__(self):
        self._records = {}

    def put(self, record):
        record = _copy(record)
        key = _match_key(record)
        if key in self._records:
            # A stale matcher cannot erase a person's decision.
            confirmation = self._records[key].confirmation
            record = MatchRecord.model_validate({
                **record.model_dump(), "confirmation": confirmation,
                "auto_bind": record.auto_bind and confirmation != "rejected",
            })
        self._records[key] = record

    def for_demand(self, demand_id, version, generation=None):
        return [_copy(record) for record in self._records.values()
                if (record.demand_id, record.demand_version) == (demand_id, version)
                and (generation is None or record.generation == generation)]

    def set_confirmation(self, demand_id, version, indicator_key, series, generation, confirmation):
        key = (demand_id, version, indicator_key, _series_key(series), generation)
        record = self._records[key]
        updated = {**record.model_dump(), "confirmation": confirmation}
        if confirmation == "rejected":
            updated["auto_bind"] = False
        self._records[key] = MatchRecord.model_validate(updated)
