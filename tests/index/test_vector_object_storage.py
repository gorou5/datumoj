"""LanceDB 0.40.0 on moto HTTP S3, not a claim about real R2.

The WSGI gate interrupts a writer after data upload, before manifest publication.
All credentials are synthetic; reports contain only counts and relative keys.
"""

import json
import logging
import os
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version

import boto3
import lancedb
import pytest
from moto.server import ThreadedMotoServer


@pytest.fixture
def remote(monkeypatch):
    assert version("lancedb") == "0.40.0"
    assert version("moto") == "5.2.4"
    # Ignore ambient cloud credentials and never invoke AWS metadata discovery.
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "synthetic-test-id")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "synthetic-test-secret")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    logger = logging.getLogger("werkzeug")
    previous_level = logger.level
    logger.setLevel(logging.CRITICAL)
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    options = {
        "aws_access_key_id": os.environ["R2_ACCESS_KEY_ID"],
        "aws_secret_access_key": os.environ["R2_SECRET_ACCESS_KEY"],
        "aws_endpoint": f"http://{host}:{port}", "aws_region": "us-east-1",
        "allow_http": "true", "aws_virtual_hosted_style_request": "false",
    }
    client = boto3.client("s3", endpoint_url=options["aws_endpoint"], region_name="us-east-1",
                          aws_access_key_id=options["aws_access_key_id"],
                          aws_secret_access_key=options["aws_secret_access_key"])
    bucket = f"synthetic-{uuid.uuid4().hex}"
    client.create_bucket(Bucket=bucket)
    uri = f"s3://{bucket}/experiment"
    entered, release = threading.Event(), threading.Event()
    gate = {"enabled": False}
    requests = []
    app = server._server.app

    def instrument(environ, start_response):
        path = environ["PATH_INFO"]
        method = environ["REQUEST_METHOD"]
        if gate["enabled"] and method == "PUT" and path.endswith(".manifest"):
            entered.set()
            if not release.wait(30):
                start_response("503 Service Unavailable", [("Content-Length", "0")])
                return [b""]
            # The interrupted commit is rejected, even if the client disconnected.
            start_response("503 Service Unavailable", [("Content-Length", "0")])
            return [b""]

        def response(status, headers, exc_info=None):
            requests.append({"method": method, "status": int(status.split()[0]),
                             "range_request": bool(environ.get("HTTP_RANGE")),
                             "upload_bytes": int(environ.get("CONTENT_LENGTH") or 0),
                             "download_bytes": (int(dict(headers).get("Content-Length", 0))
                                                if method == "GET" else 0)})
            return start_response(status, headers, exc_info)

        return app(environ, response)

    server._server.app = instrument

    def connect():
        return lancedb.connect(uri, storage_options=options, session=lancedb.Session())

    def objects():
        return {o["Key"].removeprefix("experiment/"): o["Size"]
                for o in client.list_objects_v2(Bucket=bucket, Prefix="experiment/").get("Contents", [])}

    try:
        yield {"connect": connect, "objects": objects, "options": options, "uri": uri,
               "gate": gate, "entered": entered, "release": release, "requests": requests}
    finally:
        release.set()
        client.close()
        server.stop()
        logger.setLevel(previous_level)


def row(i):
    return {"id": i, "source_id": "synthetic", "vector": [float(i), 1., 2., 3.]}


def ids(table):
    return sorted(r["id"] for r in table.search().select(["id"]).to_list())


def report(remote, operation, since=0):
    requests = list(remote["requests"][since:])
    objects = remote["objects"]()
    print(json.dumps({"operation": operation, "object_count": len(objects),
                      "object_bytes": sum(objects.values()),
                      "data_bytes": sum(s for k, s in objects.items() if "/data/" in k),
                      "requests": len(requests),
                      "upload_bytes": sum(r["upload_bytes"] for r in requests),
                      "download_bytes": sum(r["download_bytes"] for r in requests),
                      "range_requests": sum(r["range_request"] for r in requests),
                      "conflict_responses": sum(r["status"] in (409, 412) for r in requests)}, sort_keys=True))


def test_create_append_delete_search_and_reopen(remote):
    table = remote["connect"]().create_table("vectors", data=[row(0), row(1)])
    assert ids(table) == [0, 1]
    assert remote["objects"]()
    before = remote["objects"]()
    start = len(remote["requests"])
    table.add([row(2)])
    report(remote, "append_only", start)
    after = remote["objects"]()
    assert all(after.get(key) == size for key, size in before.items() if "/data/" in key)
    assert len([k for k in after if "/data/" in k]) == 2
    assert ids(table) == [0, 1, 2]
    start = len(remote["requests"])
    table.delete("id = 1")
    report(remote, "delete_only", start)
    deleted = remote["objects"]()
    assert {k: s for k, s in deleted.items() if "/data/" in k} == {
        k: s for k, s in after.items() if "/data/" in k}
    assert any("/_deletions/" in key for key in deleted)
    assert ids(table) == [0, 2]
    result = table.search(row(2)["vector"]).metric("l2").limit(1).to_list()
    assert result[0]["id"] == 2 and result[0]["_distance"] == 0
    reopened = remote["connect"]().open_table("vectors")
    assert ids(reopened) == [0, 2]
    assert len(reopened.list_versions()) == 3
    start = len(remote["requests"])
    cold_table = remote["connect"]().open_table("vectors")
    assert cold_table.search(row(2)["vector"]).metric("l2").limit(1).to_list()[0]["id"] == 2
    assert any(r["method"] == "GET" for r in remote["requests"][start:])
    assert all(r["method"] in ("GET", "HEAD") for r in remote["requests"][start:])
    report(remote, "cold_search", start)
    report(remote, "create_append_delete_search_reopen")


def test_two_connections_concurrent_appends(remote):
    remote["connect"]().create_table("vectors", data=[row(0)])
    tables = [remote["connect"]().open_table("vectors") for _ in range(2)]
    assert tables[0].version == tables[1].version == 1
    barrier = threading.Barrier(2)

    def append(i):
        barrier.wait(timeout=10)
        tables[i].add([row(i + 1)])

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(append, range(2)))
    reopened = remote["connect"]().open_table("vectors")
    assert ids(reopened) == [0, 1, 2]
    assert reopened.version == 3
    report(remote, "two_connections_concurrent_appends")


def test_killed_writer_before_manifest_reopens_previous_commit(remote, tmp_path):
    remote["connect"]().create_table("vectors", data=[row(0)])
    before = remote["objects"]()
    remote["gate"]["enabled"] = True
    script = """
import json, os, lancedb
config = json.loads(os.environ['SYNTHETIC_LANCE_CONFIG'])
table = lancedb.connect(config['uri'], storage_options=config['options']).open_table('vectors')
table.add([{'id': 1, 'source_id': 'synthetic', 'vector': [1., 1., 2., 3.]}])
"""
    env = {**os.environ, "SYNTHETIC_LANCE_CONFIG": json.dumps({"uri": remote["uri"], "options": remote["options"]})}
    process = subprocess.Popen([sys.executable, "-c", script], env=env, cwd=tmp_path,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert remote["entered"].wait(20), "writer did not reach manifest gate"
        during = remote["objects"]()
        assert len([k for k in during if "/data/" in k]) > len([k for k in before if "/data/" in k])
        process.kill()
        process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        remote["release"].set()
        remote["gate"]["enabled"] = False
    reopened = remote["connect"]().open_table("vectors")
    assert reopened.version == 1
    assert ids(reopened) == [0]
    reopened.add([row(2)])
    assert ids(remote["connect"]().open_table("vectors")) == [0, 2]
    report(remote, "killed_before_manifest_recovery")
