"""Memory implementations must qualify against the public protocol suites."""

import pytest

from datumoj.eltiro.testing.contract_suites import (
    DemandRegistrySuite,
    MatchStoreSuite,
    ModelCacheSuite,
    ModelProviderSuite,
    ModelRouterSuite,
    ProfileStoreSuite,
)
from datumoj.eltiro.testing.fakes import (
    FakeDemandRegistry,
    FakeMatchStore,
    FakeModelCache,
    FakeModelProvider,
    FakeModelRouter,
    FakeProfileStore,
)


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


class TestFakeModelRouter(ModelRouterSuite):
    @pytest.fixture
    def make_router(self):
        return FakeModelRouter


class TestFakeModelProvider(ModelProviderSuite):
    @pytest.fixture
    def make_provider(self):
        return FakeModelProvider


class TestFakeModelCache(ModelCacheSuite):
    @pytest.fixture
    def make_cache(self):
        return FakeModelCache
