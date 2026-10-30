"""Report validation and atomic file publication; no network or production state."""

import json

import pytest

from datumoj.eltiro import gaps
from datumoj.eltiro.models import ConsumerConfig
from datumoj.eltiro.testing.fakes import FakeDemandRegistry, FakeMatchStore, FakeProfileStore
from datumoj.eltiro.testing.samples import make_demand, make_match, make_profile, make_series
from datumoj.rikolto.testing.fakes import FakeRegistry
from datumoj.rikolto.testing.samples import make_spec


@pytest.fixture
def options(tmp_path):
    consumer = ConsumerConfig.model_validate({
        "id": "example-consumer", "inbox": "consumer/inbox", "outbox": "consumer/outbox",
        "allowed_sources": ["intl-wdi"], "models": {"consumer": "example-consumer",
        "authorized_sources": ["intl-wdi"]}, "max_active_demands": 5,
    })
    return {"consumer": consumer, "matches": FakeMatchStore(), "profiles": FakeProfileStore(),
            "registry": FakeRegistry([make_spec("intl-wdi")]), "data_dir": tmp_path}


def test_report_files_are_atomic_and_temp_files_removed_on_replace_failure(options, monkeypatch):
    first = gaps.gap_report(make_demand(), 1, **options)
    path = options["data_dir"] / "eltiro/gaps/example-demand-v1-g1.json"
    before = path.read_bytes()
    original = gaps.os.replace
    observed = []

    def fail(source, target):
        assert source.parent == target.parent
        assert json.loads(source.read_text())["generation"] == 1
        assert target.read_bytes() == before
        observed.append(target)
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(gaps.os, "replace", fail)
    with pytest.raises(OSError, match="synthetic disk failure"):
        gaps.gap_report(make_demand(), 1, **options)
    assert observed == [path] and path.read_bytes() == before
    assert list(path.parent.iterdir()) == [path]
    monkeypatch.setattr(gaps.os, "replace", original)
    assert gaps.gap_report(make_demand(), 1, **options) == first


@pytest.mark.parametrize("bad_generation", [0, -1, True, "1"])
def test_invalid_generation_has_no_output(options, bad_generation):
    with pytest.raises(ValueError):
        gaps.gap_report(make_demand(), bad_generation, **options)
    assert not list(options["data_dir"].rglob("*.json"))


def test_ownership_and_constructed_invalid_id_revalidated(options):
    for request in (make_demand(consumer="somebody-else"), make_demand().model_copy(update={"id": "../escape"})):
        with pytest.raises(ValueError):
            gaps.gap_report(request, 1, **options)
    assert not list(options["data_dir"].rglob("*.json"))


@pytest.mark.parametrize("profile", [None, make_profile(generation=2),
                                     make_profile(series=make_series(table_key="different"))])
def test_dangling_or_mismatched_profile_is_an_error_not_invented_availability(options, profile):
    options["matches"].put(make_match())

    class BrokenReadView:
        def get(self, generation, series):
            assert generation == 1
            return profile

    options["profiles"] = BrokenReadView()
    with pytest.raises(ValueError, match="profile"):
        gaps.gap_report(make_demand(), 1, **options)
    assert not list(options["data_dir"].rglob("*.json"))


@pytest.mark.parametrize("directory", ["gaps", "reports"])
@pytest.mark.parametrize("redirect", ["outbox", "outside-data"])
def test_symlink_cannot_redirect_report_directory(options, tmp_path, directory, redirect):
    root = tmp_path / "data"
    root.mkdir()
    options["data_dir"] = root
    target = root / "consumer/outbox" if redirect == "outbox" else tmp_path / "unrelated"
    target.mkdir(parents=True)
    (root / "eltiro").mkdir()
    (root / "eltiro" / directory).symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="directory"):
        if directory == "gaps":
            gaps.gap_report(make_demand(), 1, **options)
        else:
            config = options.pop("consumer")
            gaps.aggregate_gaps(1, demands=FakeDemandRegistry(), consumers={config.id: config}, **options)
    assert list(target.iterdir()) == []


def test_all_active_contexts_validated_before_publishing_aggregate(options):
    config = options.pop("consumer")
    demands = FakeDemandRegistry()
    demands.register(make_demand())
    demands.register(make_demand(id="second", consumer="missing-consumer"))
    with pytest.raises(ValueError, match="no consumer"):
        gaps.aggregate_gaps(1, demands=demands, consumers={config.id: config}, **options)
    assert not list(options["data_dir"].rglob("*.json"))


def test_empty_aggregate_and_markdown_escapes_untrusted_concepts(options):
    config = options.pop("consumer")
    demands = FakeDemandRegistry()
    report = gaps.aggregate_gaps(1, demands=demands, consumers={config.id: config}, **options)
    assert report["active_demands"] == 0
    assert report["by_concept"] == report["by_source"] == []
    concept = "GDP\n```\n<script>untrusted data</script>"
    demands.register(make_demand(indicators=[{"key": "gdp", "concept": concept}]))
    report = gaps.aggregate_gaps(1, demands=demands, consumers={config.id: config}, **options)
    markdown = (options["data_dir"] / "eltiro/reports/gaps-g1.md").read_text()
    assert "<script>" not in markdown and markdown.count("```json") == 2
    assert report["by_concept"][0]["concept"] == concept
    assert gaps.aggregate_gaps(1, demands=demands, consumers={config.id: config}, **options) == report
