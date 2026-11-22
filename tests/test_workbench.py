import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from scripts.generate_examples import SAMPLE_RESULT, make_sample
from workbench.app import create_app
from workbench.domain import compare, decode_json, validate_schema


@pytest.fixture
def client(tmp_path, backend_server):
    app = create_app(tmp_path)
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + app.state.store.token
        c.headers["X-Workbench"] = "1"
        seed_test_workspace(c, backend_server[0])
        yield c


def seed_test_workspace(c, server):
    """Only tests create fixtures; the production startup contains no simulated backend."""
    real_provider(c, server)
    file = c.post("/api/files", files={"file": ("invoice.png", make_sample().getvalue())}).json()
    dataset = c.post("/api/datasets", json={"name": "Invoice validation"}).json()
    c.post("/api/datasets/" + dataset["id"] + "/samples", json={"file_id": file["id"], "gold": SAMPLE_RESULT})


def bootstrap(client):
    return client.get("/api/bootstrap").json()


def submit(client, **overrides):
    b = bootstrap(client)
    body = {
        "template_id": b["templates"][0]["id"],
        "version": b["templates"][0]["version"],
        "provider_id": b["providers"][0]["id"],
        "file_ids": [b["files"][0]["id"]],
    }
    body.update(overrides)
    return client.post("/api/jobs", json=body)


def wait_job(client, key):
    for _ in range(100):
        result = client.get("/api/jobs/" + key).json()
        if result["status"] not in {"queued", "running"}:
            return result
        time.sleep(0.03)
    pytest.fail("worker did not finish")


def pdf_bytes(count=2):
    image = Image.new("RGB", (150, 200), "white")
    out = io.BytesIO()
    image.save(out, format="PDF", save_all=True, append_images=[image] * (count - 1))
    return out.getvalue()


def test_private_files_and_api_require_auth(client):
    file = bootstrap(client)["files"][0]
    client.headers.pop("Authorization")
    assert client.get("/api/bootstrap").status_code == 401
    assert client.get("/api/files/" + file["id"] + "/pages/1").status_code == 401
    assert client.get("/api/files/" + file["id"] + "/download").status_code == 401
    assert client.get("/healthz").status_code == 200


def test_login_cookie_csrf_and_cross_origin(client):
    token = client.app.state.store.token
    client.headers.pop("Authorization")
    assert client.post("/api/session", json={"token": "wrong"}).status_code == 401
    response = client.post("/api/session", json={"token": token})
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert client.cookies["workbench_session"] != token
    assert client.get("/api/bootstrap").status_code == 200
    client.headers.pop("X-Workbench")
    assert client.post("/api/datasets", json={"name": "test"}).status_code == 403
    client.headers["X-Workbench"] = "1"
    assert (
        client.post(
            "/api/datasets", json={"name": "test"}, headers={"Origin": "https://evil.test"}
        ).status_code
        == 403
    )


def test_model_protocol_closed_loop(client):
    task = wait_job(client, submit(client).json()["id"])
    assert task["status"] == "succeeded"
    result = task["results"][0]
    assert result["data"] == SAMPLE_RESULT
    assert result["backend"] == "openai"
    assert result["pages"] == [1]
    assert result["usage"]["completion_tokens"] == 8
    assert "confidence" not in result


def test_versions_are_immutable_and_job_snapshots_are_fixed(client):
    t = bootstrap(client)["templates"][0]
    task = submit(client).json()
    body = {k: t[k] for k in ("name", "schema", "system_prompt", "user_prompt")}
    body["user_prompt"] = "New version {{schema}}"
    saved = client.post("/api/templates/" + t["id"] + "/versions", json=body).json()
    assert saved["version"] == 2
    versions = client.get("/api/templates/" + t["id"] + "/versions").json()
    assert versions[1]["user_prompt"] == t["user_prompt"]
    assert wait_job(client, task["id"])["payload"]["template"]["version"] == 1


def test_idempotency_and_conflict(client):
    b = bootstrap(client)
    payload = {
        "template_id": b["templates"][0]["id"],
        "version": 1,
        "provider_id": b["providers"][0]["id"],
        "file_ids": [b["files"][0]["id"]],
    }
    first = client.post("/api/jobs", json=payload, headers={"Idempotency-Key": "stable"})
    second = client.post("/api/jobs", json=payload, headers={"Idempotency-Key": "stable"})
    assert first.json()["id"] == second.json()["id"]
    payload["mode"] = "page"
    assert client.post("/api/jobs", json=payload, headers={"Idempotency-Key": "stable"}).status_code == 409


def test_pdf_page_mode_joint_mode_and_range(client):
    uploaded = client.post("/api/files", files={"file": ("two.pdf", pdf_bytes(), "application/pdf")}).json()
    assert len(uploaded["pages"]) == 2
    assert "storage_key" not in uploaded
    separate = wait_job(client, submit(client, file_ids=[uploaded["id"]], mode="page").json()["id"])
    assert [r["pages"] for r in separate["results"]] == [[1], [2]]
    joint = wait_job(client, submit(client, file_ids=[uploaded["id"]]).json()["id"])
    assert joint["results"][0]["pages"] == [1, 2]
    selected = wait_job(client, submit(client, file_ids=[uploaded["id"]], pages=[2]).json()["id"])
    assert selected["results"][0]["pages"] == [2]
    assert submit(client, file_ids=[uploaded["id"]], pages=[3]).status_code == 422
    assert client.get("/api/files/" + uploaded["id"] + "/pages/2").headers["content-type"] == "image/png"
    assert client.get("/api/files/" + uploaded["id"] + "/pages/3").status_code == 404


def test_pdf_limits_and_upload_validation(client):
    nine = client.post("/api/files", files={"file": ("nine.pdf", pdf_bytes(9))}).json()
    assert submit(client, file_ids=[nine["id"]]).status_code == 422
    assert submit(client, file_ids=[nine["id"]], mode="page").status_code == 200
    assert client.post("/api/files", files={"file": ("too-many.pdf", pdf_bytes(21))}).status_code == 422
    assert client.post("/api/files", files={"file": ("bad.png", b"not image")}).status_code == 422
    assert client.post("/api/files", files={"file": ("empty.png", b"")}).status_code == 422
    assert submit(client, file_ids=["../../etc/passwd"]).status_code == 422
    assert submit(client, file_ids=[bootstrap(client)["files"][0]["id"]] * 2).status_code == 422


def test_prompt_controlled_variables_and_no_execution(client):
    t = bootstrap(client)["templates"][0]
    body = {k: t[k] for k in ("name", "schema", "system_prompt", "user_prompt")}
    response = client.post("/api/prompt-preview", json=body)
    assert "sample.pdf" in response.json()["user"]
    body["user_prompt"] = "{{__import__('os')}}"
    assert client.post("/api/prompt-preview", json=body).status_code == 422


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "$ref": "http://example.test/schema"},
        {"type": "object", "properties": {"bad-key": {"type": "string"}}},
        {"type": "object", "required": ["missing"]},
        {"type": "object", "properties": {"rows": {"type": "array"}}},
    ],
)
def test_schema_rejects_unsupported_or_unsafe_shapes(schema):
    with pytest.raises(ValueError):
        validate_schema(schema)


def test_json_parsing_preserves_values_and_rejects_trailing_text():
    assert decode_json('```json\n{"text":"True False None"}\n```') == {"text": "True False None"}
    for invalid in ('{"x":1} narrative', "[1,2]", '{"x": True}'):
        with pytest.raises(ValueError):
            decode_json(invalid)


def test_scoring_missing_failures_booleans_and_empty_arrays():
    result = compare({"a": "x", "rows": [{"n": 1}], "empty": []}, {"a": "x", "rows": [{"n": True}]})
    assert (result["matched"], result["total"]) == (1, 3)
    assert compare({"a": 1, "b": None}, None)["total"] == 2
    assert compare({"a": 1, "b": None}, None)["matched"] == 0
    assert compare({"a": 1}, {"a": 1.0})["matched"] == 1


def test_json_pointer_paths_do_not_collide():
    result = compare({"a/b": 1, "a": {"b": 2}, "a~b": 3}, {})
    assert result["total"] == 3
    assert {r["path"] for r in result["fields"]} == {"/a~1b", "/a/b", "/a~0b"}


def test_malformed_schema_returns_validation_error(client):
    body = {"name": "bad", "schema": {"type": "object", "required": None}}
    assert client.post("/api/templates", json=body).status_code == 422
    body["schema"] = {"type": "object", "description": 5}
    assert client.post("/api/templates", json=body).status_code == 422


def test_transparent_images_render_on_white(client):
    image = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    file = client.post("/api/files", files={"file": ("transparent.png", buffer.getvalue())}).json()
    preview = client.get("/api/files/" + file["id"] + "/pages/1")
    assert Image.open(io.BytesIO(preview.content)).getpixel((0, 0)) == (255, 255, 255)


def test_evaluation_preflight_rolls_back_entire_batch(tmp_path, backend_server):
    app = create_app(tmp_path, start_worker=False)
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + app.state.store.token
        seed_test_workspace(c, backend_server[0])
        b = bootstrap(c)
        dataset = b["datasets"][0]["id"]
        nine = c.post("/api/files", files={"file": ("nine.pdf", pdf_bytes(9))}).json()
        c.post("/api/datasets/" + dataset + "/samples", json={"file_id": nine["id"], "gold": {"x": 1}})
        response = c.post(
            "/api/datasets/" + dataset + "/runs",
            json={"template_id": b["templates"][0]["id"], "provider_id": b["providers"][0]["id"]},
        )
        assert response.status_code == 422
        assert c.get("/api/jobs").json() == []
        assert c.get("/api/runs").json() == []


def test_source_release_allowlist_excludes_runtime_secrets():
    from scripts.build_release import ROOT, release_files

    files = [str(p.relative_to(ROOT)) for p in release_files()]
    assert "workbench/static/app.js" in files
    assert "LICENSE" in files
    assert not any(p.startswith(("data/", ".venv", "dist/")) for p in files)
    assert not any(p.endswith((".sqlite3", ".onnx", ".pyc")) for p in files)


def test_dataset_evaluation_is_snapshot_and_failure_in_denominator(client):
    b = bootstrap(client)
    ds = b["datasets"][0]["id"]
    body = {"template_id": b["templates"][0]["id"], "version": 1, "provider_id": b["providers"][0]["id"]}
    run = client.post("/api/datasets/" + ds + "/runs", json=body).json()
    wait_job(client, run["samples"][0]["job_id"])
    report = client.get("/api/runs/" + run["id"]).json()
    assert report["accuracy"] == 1 and "simulated" not in report
    client.post(
        "/api/datasets/" + ds + "/samples", json={"file_id": b["files"][0]["id"], "gold": {"new": "field"}}
    )
    assert len(client.get("/api/runs/" + run["id"]).json()["samples"]) == 1
    store = client.app.state.store
    store.execute("UPDATE jobs SET status='failed',results='[]' WHERE id=?", (run["samples"][0]["job_id"],))
    failed = client.get("/api/runs/" + run["id"]).json()
    assert failed["total"] == report["total"] and failed["accuracy"] == 0


@pytest.fixture
def backend_server():
    class Handler(BaseHTTPRequestHandler):
        output = json.dumps(SAMPLE_RESULT)
        status = 200
        received = []

        def log_message(self, *args):
            pass

        def do_GET(self):
            body = json.dumps({"data": [{"id": "test-vision"}]}).encode()
            self.send_response(self.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.received.append((self.path, self.headers.get("Authorization"), data))
            body = json.dumps(
                {
                    "choices": [{"finish_reason": "stop", "message": {"content": self.output}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 8},
                }
            ).encode()
            self.send_response(self.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, Handler
    server.shutdown()
    thread.join()
    server.server_close()


def real_provider(client, server):
    return client.post(
        "/api/providers",
        json={
            "name": "Protocol test",
            "kind": "openai",
            "model": "test-vision",
            "base_url": "http://127.0.0.1:" + str(server.server_port) + "/v1",
            "api_key": "private-test-key",
        },
    ).json()


def test_discover_models_and_select_default_without_inference(client, backend_server):
    server, handler = backend_server
    response = client.post(
        "/api/providers/discover",
        json={
            "name": "Probe",
            "base_url": "http://127.0.0.1:" + str(server.server_port),
            "api_key": "test-probe-secret",
        },
    )
    assert response.status_code == 200
    assert response.json()["models"] == [{"id": "test-vision"}]
    assert handler.received == []
    provider = real_provider(client, server)
    assert client.post("/api/providers/" + provider["id"] + "/default", json={}).status_code == 200
    assert bootstrap(client)["default_provider_id"] == provider["id"]
    handler.status = 401
    response = client.post(
        "/api/providers/discover",
        json={
            "name": "Probe",
            "base_url": "http://127.0.0.1:" + str(server.server_port),
            "api_key": "test-probe-secret",
        },
        headers={"Accept-Language": "en"},
    )
    assert response.status_code == 502
    assert "HTTP 401" in response.text and "test-probe-secret" not in response.text


def test_openai_image_protocol_and_secret_redaction(client, backend_server):
    server, handler = backend_server
    provider = real_provider(client, server)
    assert provider["has_api_key"] and "api_key" not in provider
    task = wait_job(client, submit(client, provider_id=provider["id"]).json()["id"])
    assert task["status"] == "succeeded"
    assert task["results"][0]["backend"] == "openai"
    assert task["results"][0]["usage"]["prompt_tokens"] == 12
    path, authorization, payload = handler.received[0]
    assert path == "/v1/chat/completions" and authorization == "Bearer private-test-key"
    assert payload["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "private-test-key" not in client.get("/api/bootstrap").text
    assert "private-test-key" not in client.get("/api/jobs").text


@pytest.mark.parametrize(
    "output,status,expected",
    [("not JSON", 200, "failed"), ('{"total":5}', 200, "failed"), ("sensitive-body", 401, "failed")],
)
def test_bad_model_outputs_are_not_reported_as_success(client, backend_server, output, status, expected):
    server, handler = backend_server
    handler.output, handler.status = output, status
    provider = real_provider(client, server)
    task = wait_job(client, submit(client, provider_id=provider["id"]).json()["id"])
    assert task["status"] == expected
    assert task["results"][0]["status"] in {"failed", "invalid"}
    assert "sensitive-body" not in json.dumps(task)


def test_restart_recovers_running_and_keeps_checkpoint(tmp_path, backend_server):
    app = create_app(tmp_path, start_worker=False)
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + app.state.store.token
        seed_test_workspace(c, backend_server[0])
        task = submit(c).json()
        store = app.state.store
        checkpoint = [{"status": "succeeded", "data": SAMPLE_RESULT, "pages": [1], "backend": "openai"}]
        store.execute(
            "UPDATE jobs SET status='running',results=? WHERE id=?", (json.dumps(checkpoint), task["id"])
        )
    with TestClient(create_app(tmp_path)) as c:
        c.headers["Authorization"] = "Bearer " + store.token
        task = wait_job(c, task["id"])
        assert task["status"] == "succeeded" and task["results"] == checkpoint


def test_cancel_queued_and_retry_snapshot(tmp_path, backend_server):
    app = create_app(tmp_path, start_worker=False)
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + app.state.store.token
        seed_test_workspace(c, backend_server[0])
        task = submit(c).json()
        cancelled = c.post("/api/jobs/" + task["id"] + "/cancel", json={}).json()
        assert cancelled["status"] == "cancelled"
        retried = c.post("/api/jobs/" + task["id"] + "/retry", json={}).json()
        assert retried["id"] != task["id"] and retried["payload"] == task["payload"]
        assert retried["status"] == "queued"


def test_workspace_prevents_two_workers(tmp_path):
    with TestClient(create_app(tmp_path)):
        with pytest.raises(RuntimeError, match="工作空间"):
            with TestClient(create_app(tmp_path)):
                pass


def test_ui_assets_and_security_headers(client):
    page = client.get("/")
    assert "文档提取工作台" in page.text
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/assets/style.css").status_code == 200
    assert client.get("/assets/../../data/access-token").status_code == 404
