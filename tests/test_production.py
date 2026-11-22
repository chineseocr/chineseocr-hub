"""Production-path regressions: real profiles, upgrades, locale and secret boundaries."""

import json
import re
import time
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from workbench.app import create_app
from workbench.domain import ProviderInput
from workbench.store import Store, uid

ASSETS = Path(__file__).resolve().parents[1] / "workbench/static"


def test_clean_workspace_has_no_precomputed_outputs(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + app.state.store.token
        data = client.get("/api/bootstrap").json()
        assert len(data["templates"]) == 1
        assert data["providers"] == data["files"] == data["datasets"] == []
        assert data["default_provider_id"] is None
        assert client.post("/api/providers", json={"name": "old", "kind": "demo"}).status_code == 422


def test_service_root_and_nonbreaking_space_are_normalized():
    provider = ProviderInput(name="Vision", base_url=" https://vision.example.test:8443\u00a0")
    assert provider.base_url == "https://vision.example.test:8443/v1"
    assert ProviderInput(name="Vision", base_url="https://vision.example.test/v1/").base_url.endswith("/v1")


def test_environment_bootstrap_is_idempotent_and_never_returns_key(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_MODEL_BASE_URL", "https://vision.example.test")
    monkeypatch.setenv("WORKBENCH_MODEL_NAME", "vision-v1")
    monkeypatch.setenv("WORKBENCH_MODEL_API_KEY", "test-bootstrap-secret")
    monkeypatch.setenv("WORKBENCH_MODEL_LABEL", "")
    for _ in range(2):
        app = create_app(tmp_path)
        with TestClient(app) as client:
            client.headers["Authorization"] = "Bearer " + app.state.store.token
            response = client.get("/api/bootstrap")
            data = response.json()
            assert len(data["providers"]) == 1
            assert data["default_provider_id"] == data["providers"][0]["id"]
            assert data["providers"][0]["name"] == "vision-v1"
            assert "test-bootstrap-secret" not in response.text


def test_old_fixed_output_records_are_archived_not_relabelled(tmp_path):
    store = Store(tmp_path)
    provider = store.put("providers", {"name": "Legacy", "kind": "demo"})
    job_id, now = uid(), time.time()
    payload = {"provider_id": provider, "simulated": True}
    store.execute(
        "INSERT INTO jobs(id,status,payload,created,updated) VALUES(?,?,?,?,?)",
        (job_id, "queued", json.dumps(payload), now, now),
    )
    run_id = store.put("runs", {"samples": [{"job_id": job_id}], "dataset_name": "Legacy"})
    app = create_app(tmp_path)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + app.state.store.token
        assert client.get("/api/bootstrap").json()["providers"] == []
        assert client.get("/api/jobs").json() == []
        assert client.get("/api/runs").json() == []
        assert client.get("/api/jobs/" + job_id).status_code == 410
        assert client.get("/api/runs/" + run_id).status_code == 410
        assert client.post("/api/jobs/" + job_id + "/retry", json={}).status_code == 410
    assert store.job(job_id)["payload"] == payload
    assert store.job(job_id)["status"] == "cancelled"
    assert store.get("providers", provider)["kind"] == "demo"


def test_invalid_environment_is_redacted_and_releases_workspace_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_MODEL_BASE_URL", "https://vision.example.test")
    monkeypatch.setenv("WORKBENCH_MODEL_NAME", "vision")
    secret = "private-config-secret-" * 150
    monkeypatch.setenv("WORKBENCH_MODEL_API_KEY", secret)
    with pytest.raises(ValueError) as error:
        with TestClient(create_app(tmp_path)):
            pass
    assert "private-config-secret" not in str(error.value)
    monkeypatch.setenv("WORKBENCH_MODEL_API_KEY", "valid-test-key")
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/healthz").status_code == 200


def test_api_validation_hides_secret_inputs_and_respects_english(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/api/bootstrap", headers={"Accept-Language": "en"})
        assert response.json()["detail"] == "Enter your workspace access token first."
        client.headers.update({"Authorization": "Bearer " + app.state.store.token, "Accept-Language": "en"})
        response = client.post(
            "/api/providers",
            json={"name": "Invalid", "base_url": "ftp://invalid", "api_key": "must-not-echo-this"},
        )
        assert response.status_code == 422
        assert "must-not-echo-this" not in response.text
        assert "HTTP(S) model URL" in response.text
        assert "input" not in response.json()["detail"][0]


def test_all_static_locale_keys_have_english_values_and_no_fixed_output_ui():
    catalog = json.loads((ASSETS / "locales/en.json").read_text())
    html = (ASSETS / "index.html").read_text()
    for key in re.findall(r'data-i18n(?:-(?:placeholder|aria-label|alt))?="([^"]+)"', html):
        import html as html_module

        assert html_module.unescape(key) in catalog
    for file in ASSETS.rglob("*"):
        if file.suffix in {".js", ".html", ".json"}:
            text = file.read_text()
            assert not re.search(r"demo|演示|模拟", text, re.I), file.name


def test_static_interface_does_not_leave_unlocalized_english_labels():
    """Catch missing translation markers, including brand/eyebrow text outside form labels."""

    class Labels(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []
            self.unmarked = []

        def handle_starttag(self, tag, attrs):
            if tag not in {"input", "meta", "link", "br", "img", "hr"}:
                self.stack.append((tag, dict(attrs)))

        def handle_endtag(self, tag):
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index][0] == tag:
                    del self.stack[index:]
                    break

        def handle_data(self, text):
            if any(
                "data-i18n" in attrs or tag in {"script", "style", "code", "pre", "textarea"}
                for tag, attrs in self.stack
            ):
                return
            label = text.strip()
            # Language self-name, logo, version and standard export format stay literal.
            if re.search(r"[A-Za-z]", label) and not re.fullmatch(
                r"English|D|v\d+\.\d+\.\d+(?:a\d+)?|↓ JSON", label
            ):
                self.unmarked.append(label)

    parser = Labels()
    parser.feed((ASSETS / "index.html").read_text())
    assert parser.unmarked == []
