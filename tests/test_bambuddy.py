from io import BytesIO
import json
from pathlib import Path
import runpy
import threading
from unittest.mock import Mock

from flask import Flask, jsonify, request
import pytest
import requests
from werkzeug.serving import make_server

from app import app, bambuddy_lock
from bambuddy import BambuddyClient, BambuddyConfig, BambuddyError, load_config


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def remote():
    server_app = Flask("fake-bambuddy")
    state = {"calls": [], "uploads": [], "fail_after": None, "status": 200}

    @server_app.before_request
    def record():
        state["calls"].append((request.method, request.path, request.headers.get("X-API-Key")))

    @server_app.get("/api/v1/library/folders")
    def folders():
        return jsonify([]), state["status"]

    @server_app.post("/api/v1/library/files")
    def files():
        if state["fail_after"] == len(state["uploads"]):
            return jsonify(detail="do not expose remote error bodies"), 500
        file = request.files["file"]
        state["uploads"].append({
            "name": file.filename, "content": file.read(),
            "folder": request.args.get("folder_id"), "mime": file.mimetype,
        })
        return jsonify(id=len(state["uploads"]), filename=file.filename,
                       duplicate_of=42 if len(state["uploads"]) == 2 else None)

    server = make_server("127.0.0.1", 0, server_app)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_port}"
    try:
        yield state
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.fixture
def client(tmp_path, monkeypatch, remote):
    config = tmp_path / "bambuddy.local.json"
    config.write_text(json.dumps({"url": remote["url"], "api_key": "test-private-key", "folder_id": 7}))
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setitem(app.config, "BAMBUDDY_CONFIG", config)
    with app.test_client() as client:
        yield client


@pytest.fixture
def design(client):
    response = client.post("/api/generate", data={
        "image": (BytesIO((ROOT / "Demo-Images/bat_pteradactyl.png").read_bytes()), "bat.png"),
    })
    assert response.status_code == 200
    return response.json


def test_status_connection_and_no_secret_in_browser(client, remote):
    status = client.get("/api/bambuddy")
    assert status.json["configured"] is True
    assert status.json["library_url"] == remote["url"] + "/files"
    assert "test-private-key" not in status.get_data(as_text=True)
    assert remote["calls"] == []
    response = client.post("/api/bambuddy/test", json={})
    assert response.status_code == 200
    assert remote["calls"] == [("GET", "/api/v1/library/folders", "test-private-key")]


@pytest.mark.parametrize("kind,count", [("pair", 2), ("cutter", 1), ("stamp", 1)])
def test_send_real_multipart_stls(client, remote, design, kind, count):
    response = client.post("/api/bambuddy/send", json={"design_id": design["id"], "kind": kind})
    assert response.status_code == 200, response.json
    assert response.json["started"] is False
    assert len(response.json["files"]) == count
    assert len(remote["uploads"]) == count
    for upload in remote["uploads"]:
        tool = "cutter" if "cutter" in upload["name"] else "stamp"
        assert kind == "pair" or tool == kind
        assert upload["content"] == client.get(design["tools"][tool]["url"]).data
        assert upload["mime"] == "model/stl"
        assert upload["folder"] == "7"
    assert all(call == ("POST", "/api/v1/library/files", "test-private-key") for call in remote["calls"])
    if kind == "pair":
        assert response.json["files"][1]["duplicate_of"] == 42


def test_partial_upload_and_lock_release(client, remote, design):
    remote["fail_after"] = 1
    response = client.post("/api/bambuddy/send", json={"design_id": design["id"], "kind": "pair"})
    assert response.status_code == 400
    assert "bat-cutter.stl" in response.json["error"]
    assert "HTTP 500" in response.json["error"]
    assert "before retrying" in response.json["error"]
    assert "do not expose" not in response.json["error"]
    assert "test-private-key" not in response.get_data(as_text=True)
    assert not bambuddy_lock.locked()


def test_auth_failure_is_explicit(client, remote):
    remote["status"] = 403
    response = client.post("/api/bambuddy/test", json={})
    assert response.status_code == 400
    assert "permissions" in response.json["error"]


def test_missing_and_malformed_config(client):
    config = Path(app.config["BAMBUDDY_CONFIG"])
    config.unlink()
    assert client.get("/api/bambuddy").json["configured"] is False
    response = client.post("/api/bambuddy/test", json={})
    assert response.status_code == 400
    assert "Cannot read" in response.json["error"]
    config.write_text("not json")
    assert client.get("/api/bambuddy").status_code == 400


@pytest.mark.parametrize("change", [
    {"url": "https://example.com"}, {"url": "http://8.8.8.8:8000"},
    {"url": "http://user:password@192.168.1.2"}, {"url": "file:///tmp/file"},
    {"url": "http://192.168.1.2:0"}, {"url": "http://192.168.1.2:invalid"},
    {"url": "http://192.168.1.2/?key=secret"}, {"url": "http://192.168.1.2/#fragment"},
    {"api_key": "bad\nheader"}, {"api_key": None}, {"folder_id": True}, {"folder_id": 0},
])
def test_invalid_config(tmp_path, change):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"url": "http://192.168.1.2:8000", **change}))
    with pytest.raises(BambuddyError):
        load_config(path)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000", "https://192.168.1.2/base/", "http://[::1]:8000"])
def test_valid_config(tmp_path, url):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"url": url}))
    config = load_config(path)
    assert config.url == url.rstrip("/")
    assert config.api_key == ""
    assert config.folder_id is None


def test_request_validation_and_origin_protection(client, remote, design):
    assert client.post("/api/bambuddy/test", data="{}").status_code == 415
    assert client.post("/api/bambuddy/test", json=[]).status_code == 400
    assert client.post("/api/bambuddy/test", json={}, headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.get("/api/bambuddy", headers={"Host": "evil.example"}).status_code == 403
    assert client.post("/api/bambuddy/test", json={}, headers={"Origin": "http://localhost"}).status_code == 200
    remote["calls"].clear()
    for payload in ({}, {"design_id": "../bad", "kind": "pair"}, {"design_id": design["id"], "kind": []}):
        assert client.post("/api/bambuddy/send", json=payload).status_code == 400
    assert client.post("/api/bambuddy/send", json={"design_id": "0" * 32, "kind": "pair"}).status_code == 404
    bambuddy_lock.acquire()
    try:
        response = client.post("/api/bambuddy/send", json={"design_id": design["id"], "kind": "pair"})
        assert response.status_code == 409
    finally:
        bambuddy_lock.release()
    assert remote["calls"] == []


def test_client_timeout_redirect_and_response_validation(monkeypatch, tmp_path):
    session = Mock()
    session.headers = {}
    monkeypatch.setattr(requests, "Session", lambda: session)
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    client = BambuddyClient(BambuddyConfig("http://127.0.0.1:8000", "secret", None))
    session.request.side_effect = requests.Timeout("secret")
    with pytest.raises(BambuddyError, match="Could not reach"):
        client.check_connection()
    assert session.trust_env is False
    assert session.headers["X-API-Key"] == "secret"
    assert session.request.call_args.kwargs["allow_redirects"] is False
    assert session.request.call_args.kwargs["timeout"] == (5, 120)
    session.request.side_effect = None
    response = session.request.return_value
    response.status_code = 302
    with pytest.raises(BambuddyError, match="HTTP 302"):
        client.check_connection()
    response.status_code = 200
    response.json.side_effect = ValueError("secret")
    with pytest.raises(BambuddyError, match="invalid JSON"):
        client.check_connection()
    response.json.side_effect = None
    response.json.return_value = {}
    with pytest.raises(BambuddyError, match="folders response"):
        client.check_connection()
    model = tmp_path / "test.stl"
    model.write_bytes(b"test")
    with pytest.raises(BambuddyError, match="upload response"):
        client.upload(model)


def test_app_uses_requested_local_port(monkeypatch):
    run = Mock()
    monkeypatch.setattr(Flask, "run", run)
    runpy.run_path(str(ROOT / "app.py"), run_name="__main__")
    run.assert_called_once_with(host="127.0.0.1", port=5400, debug=False)
