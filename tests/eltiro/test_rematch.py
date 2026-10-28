"""Complete identities and demand-version boundaries in the reverse index."""

from datumoj.eltiro.match_store import MatchResult
from datumoj.eltiro.rematch import _ReverseIndex, _digest
from datumoj.eltiro.testing.samples import make_demand, make_match, make_profile, make_series


def test_profile_hash_excludes_only_generation():
    profile = make_profile()
    assert _digest(profile) == _digest(profile.model_copy(update={"generation": 2}))
    assert _digest(profile) != _digest(profile.model_copy(update={"unit": "USD"}))


def test_historical_series_index_uses_complete_identity():
    index = _ReverseIndex()
    demand = make_demand(indicators=[{"key": "gdp", "concept": "unrelated words"}])
    index.add(demand, [make_match(confirmation="rejected")], MatchResult([], [], False))
    profile = make_profile(names={"en": "Output total"})
    assert index.affected(profile) == {demand.id}
    different = profile.model_copy(update={"series": make_series(fixed_dimensions={"valuation": "constant"})})
    assert index.affected(different) == set()


def test_codes_positive_examples_and_preferred_sources_are_indexed():
    index = _ReverseIndex()
    demand = make_demand(indicators=[{"key": "gdp", "concept": "unrelated words", "examples": {
        "positive": ["NY.GDP.MKTP.CD"]}, "preferred_sources": ["jp-example"]}])
    index.add(demand, [], MatchResult([], [], False))
    assert index.affected(make_profile(names={"en": "Output total"})) == {demand.id}
    assert index.affected(make_profile(names={"en": "Output total"}, series=make_series(
        source_id="jp-example", indicator_code=None))) == {demand.id}
