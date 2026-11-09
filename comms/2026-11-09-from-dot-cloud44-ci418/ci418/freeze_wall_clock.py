"""Freeze only the test's initial wall-clock read, not production clocks."""
import os
from datetime import datetime


def pytest_collection_modifyitems(items):
    wall_now = datetime.fromisoformat(os.environ['MAPLE_WALL_NOW'])

    class WallDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return wall_now.astimezone(tz)

    for item in items:
        if item.name == 'test_two_cloud_generations_pull_mcp_and_operational_guards':
            item.module.datetime = WallDatetime
