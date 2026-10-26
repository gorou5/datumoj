"""Memory implementations must qualify against the public protocol suites."""

import pytest

from datumoj.eltiro.testing.contract_suites import DemandRegistrySuite, MatchStoreSuite, ProfileStoreSuite
from datumoj.eltiro.testing.fakes import FakeDemandRegistry, FakeMatchStore, FakeProfileStore


class TestFakeDemandRegistry(DemandRegistrySuite):
    @pytest.fixture
    def make_registry(self):
        return FakeDemandRegistry


class TestFakeProfileStore(ProfileStoreSuite):
    @pytest.fixture
    def make_profile_store(self):
        return FakeProfileStore


class TestFakeMatchStore(MatchStoreSuite):
    @pytest.fixture
    def make_match_store(self):
        return FakeMatchStore
