"""Catalog scale, release safety and public API boundaries using synthetic inputs."""

import io
import json
import os

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from workbench.adapters import BACKENDS
from workbench.app import create_app
from workbench.domain import schema_errors
from workbench.engine import Worker
from workbench.saas import create_saas_app


def config(code="identity-test", name="Synthetic identity"):
    return {
        "code": code,
        "category": "credentials",
        "tags": ["id_card_front"],
        "owner": "Test team",
        "template": {
            "name": name,
            "schema": {
                "type": "object",
                "properties": {"name": {"type": ["string", "null"]}},
                "required": ["name"],
                "additionalProperties": False,
            },
            "system_prompt": "Read printed identity fields only. Missing values are null.",
            "user_prompt": "{{schema}}\n{{filename}}\n{{pages}}",
        },
    }


@pytest.fixture
def studio(tmp_path):
    with TestClient(create_app(tmp_path, start_worker=False)) as client:
        client.headers["Authorization"] = "Bearer " + client.app.state.store.token
        yield client, client.app


def create(client, code="identity-test"):
    response = client.post("/api/applications", json=config(code))
    assert response.status_code == 200, response.text
    return response.json()


def model(client):
    response = client.post(
        "/api/providers",
        json={
            "name": "Synthetic protocol model",
            "model": "vision",
            "base_url": "http://127.0.0.1:19999/v1",
            "api_key": "not-a-real-key",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def upload(client):
    out = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(out, "PNG")
    response = client.post("/v1/files", files={"file": ("synthetic.png", out.getvalue(), "image/png")})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def publish(client, app, provider, version=1):
    response = client.post(
        "/api/applications/" + app["id"] + "/publish",
        json={"version": version, "provider_id": provider, "notes": "Synthetic validated configuration"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_common_credentials_install_as_editable_independent_versions(studio):
    c, app = studio
    zh = c.get("/api/application-presets").json()
    en = c.get("/api/application-presets", headers={"Accept-Language": "en"}).json()
    assert len(zh) == len(en) == 17
    assert {p["id"] for p in zh} >= {
        "identity-card-front",
        "identity-card-back",
        "passport",
        "driving-license",
        "business-license",
    }
    labels = c.get("/api/recognition/capabilities").json()["labels"]
    for preset in zh + en:
        assert preset["document_label"] in labels
        assert schema_errors(preset["schema"], dict.fromkeys(preset["schema"]["properties"])) == []
        assert "{{schema}}" in preset["user_prompt"]
        assert preset["schema"]["properties"]
    result = c.post("/api/applications/install-presets", json={}).json()
    assert len(result["created"]) == 17
    key = result["created"][0]
    template = c.get("/api/applications/" + key).json()["template"]
    template["system_prompt"] = "User edited credential rule"
    assert c.post("/api/templates/" + key + "/versions", json=template).json()["version"] == 2
    again = c.post("/api/applications/install-presets", json={}).json()
    assert again["created"] == [] and len(again["skipped"]) == 17
    assert (
        c.get("/api/applications/" + key).json()["template"]["system_prompt"] == "User edited credential rule"
    )
    assert c.get("/api/bootstrap").json()["providers"] == []
    assert c.get("/api/jobs").json() == []


def test_catalog_search_pagination_and_atomic_bulk_import_for_150_apps(studio):
    c, app = studio
    items = [config("document-" + str(n), "Document " + str(n)) for n in range(150)]
    for batch in (items[:100], items[100:]):
        assert c.post("/api/applications/import", json={"applications": batch}).status_code == 200
    page = c.get("/api/applications?category=credentials&offset=100&limit=25").json()
    assert page["total"] == 150 and len(page["items"]) == 25
    assert all(
        "template" not in entry and "system_prompt" not in json.dumps(entry) for entry in page["items"]
    )
    found = c.get("/api/applications?q=document-149").json()
    assert found["total"] == 1 and found["items"][0]["code"] == "document-149"
    assert c.get("/api/applications?q=Test%20team").json()["total"] == 150
    failed = c.post(
        "/api/applications/import", json={"applications": [config("new-before-conflict"), items[0]]}
    )
    assert failed.status_code == 409
    assert c.get("/api/applications?q=new-before-conflict").json()["total"] == 0


def test_catalog_metadata_copy_export_and_code_stability(studio):
    c, app = studio
    entry = create(c)
    key = entry["id"]
    assert c.post("/api/applications/" + key + "/metadata", json={"code": "changed-code"}).status_code == 422
    assert (
        c.post(
            "/api/applications/" + key + "/metadata",
            json={
                "code": entry["code"],
                "category": "finance",
                "tags": ["invoice"],
                "owner": "New owner",
                "description": "Review me",
            },
        ).status_code
        == 200
    )
    copied = c.post(
        "/api/applications/" + key + "/copy", json={"code": "identity-copy", "name": "Copy"}
    ).json()
    assert copied["category"] == "finance" and copied["published"] is None
    bundle = c.get("/api/applications/" + copied["id"] + "/export").json()
    assert "provider" not in json.dumps(bundle) and "release" not in json.dumps(bundle)
    assert bundle["applications"][0]["template"]["system_prompt"] == config()["template"]["system_prompt"]
    assert c.post("/api/applications", json=config()).status_code == 409


class ProtocolModel:
    def __init__(self):
        self.calls = []

    def extract(self, provider, system, user, images):
        self.calls.append((system, user))
        return {
            "raw_text": '{"name":"Synthetic Person"}',
            "usage": {"prompt_tokens": 5, "completion_tokens": 4},
            "duration_ms": 1,
        }


def test_public_api_uses_release_not_draft_and_never_exposes_configuration(studio, monkeypatch):
    c, app = studio
    entry = create(c)
    provider = model(c)
    file_id = upload(c)
    path = "/v1/apps/" + entry["code"] + "/recognitions"
    assert c.post(path, json={"file_ids": [file_id]}).status_code == 409
    release = publish(c, entry, provider)
    changed = config()["template"] | {"system_prompt": "DRAFT RULE NEVER PUBLISHED"}
    c.post("/api/templates/" + entry["id"] + "/versions", json=changed)
    assert c.get("/api/applications/" + entry["id"]).json()["has_changes"] is True
    diff = c.get("/api/applications/" + entry["id"] + "/diff?before=1&after=2").json()
    assert "DRAFT RULE" in diff["diff"]["system_prompt"]
    response = c.post(path, json={"file_ids": [file_id]}, headers={"Idempotency-Key": "safe-retry"})
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["application"]["release_id"] == release["id"]
    backend = ProtocolModel()
    monkeypatch.setitem(BACKENDS, "openai", backend)
    Worker(app.state.store).process(job["id"])
    saved = c.get("/v1/jobs/" + job["id"]).json()
    assert saved["status"] == "succeeded" and saved["results"][0]["data"]["name"] == "Synthetic Person"
    assert backend.calls[0][0] == config()["template"]["system_prompt"]
    for secret in ("not-a-real-key", "DRAFT RULE", "system_prompt", "provider_id", "raw_text"):
        assert secret not in json.dumps(saved)
    publish(c, entry, provider, 2)
    repeated = c.post(path, json={"file_ids": [file_id]}, headers={"Idempotency-Key": "safe-retry"}).json()
    assert repeated["id"] == job["id"] and repeated["application"]["version"] == 1
    assert (
        c.post(
            path, json={"file_ids": [file_id], "pages": [1]}, headers={"Idempotency-Key": "safe-retry"}
        ).status_code
        == 409
    )
    assert c.post(path, json={"file_ids": [file_id], "provider_id": provider}).status_code == 422
    assert (
        c.post(
            "/api/applications/" + entry["id"] + "/activate", json={"release_id": release["id"]}
        ).status_code
        == 200
    )
    assert c.post(path, json={"file_ids": [file_id]}).json()["application"]["version"] == 1
    assert c.post("/api/applications/" + entry["id"] + "/state", json={"disabled": True}).status_code == 200
    assert c.post(path, json={"file_ids": [file_id]}).status_code == 409
    assert c.get("/v1/jobs/" + job["id"]).status_code == 200


def test_public_upload_uses_file_size_limit_instead_of_json_limit(studio):
    c, app = studio
    out = io.BytesIO()
    Image.frombytes("RGB", (360, 360), os.urandom(360 * 360 * 3)).save(out, "PNG")
    assert len(out.getvalue()) > 256 * 1024
    assert c.post("/v1/files", files={"file": ("synthetic-noise.png", out.getvalue())}).status_code == 200
    c.headers.pop("Authorization")
    assert c.get("/v1/openapi.json").status_code == 401
    assert c.post("/v1/files", files={"file": ("denied.png", out.getvalue())}).status_code == 401


def test_releases_and_installed_prompts_survive_restart(tmp_path):
    for index in range(2):
        with TestClient(create_app(tmp_path, start_worker=False)) as c:
            c.headers["Authorization"] = "Bearer " + c.app.state.store.token
            if not index:
                entry = create(c)
                publish(c, entry, model(c))
            else:
                found = c.get("/api/applications?q=identity-test").json()["items"][0]
                assert found["id"] == entry["id"] and found["state"] == "published"


def test_saas_public_gateway_application_keys_and_resource_isolation(tmp_path, monkeypatch):
    app = create_saas_app(
        tmp_path, start_worker=False, registration=True, allowed_model_bases=["http://127.0.0.1:19999/v1"]
    )
    with TestClient(app) as c:
        c.headers["X-Workbench"] = "1"
        credentials = json.loads((tmp_path / "saas/initial-admin.json").read_text())
        assert c.post("/api/session", json=credentials).status_code == 200
        team = c.get("/api/account").json()["active_tenant_id"]
        first, second = create(c, "identity-first"), create(c, "identity-second")
        provider = model(c)
        publish(c, first, provider)
        publish(c, second, provider)
        assert (
            c.post(
                "/api/admin/tenants/" + team + "/limits",
                json={"plan": "custom", "monthly_pages": 1, "storage_bytes": 1048576, "members": 10},
            ).status_code
            == 200
        )
        old_file = upload(c)
        one = c.post("/api/api-keys", json={"name": "First only", "application_ids": [first["id"]]}).json()
        two = c.post("/api/api-keys", json={"name": "Second only", "application_ids": [second["id"]]}).json()
        assert (
            c.post("/api/api-keys", json={"name": "Foreign", "application_ids": ["missing"]}).status_code
            == 422
        )
        c.headers["Authorization"] = "Bearer " + one["token"]
        for path in ("/api/bootstrap", "/api/applications", "/api/openapi.json"):
            assert c.get(path).status_code == 403
        assert (
            c.post("/v1/apps/identity-first/recognitions", json={"file_ids": [old_file]}).status_code == 404
        )
        source = upload(c)
        task = c.post("/v1/apps/identity-first/recognitions", json={"file_ids": [source]}).json()
        assert task["status"] == "queued"
        assert c.post("/v1/apps/identity-first/recognitions", json={"file_ids": [source]}).status_code == 422
        assert c.post("/v1/apps/identity-second/recognitions", json={"file_ids": [source]}).status_code == 403
        assert c.get("/v1/jobs/" + task["id"]).status_code == 200
        schema = c.get("/v1/openapi.json").json()
        assert schema["paths"] and all(p.startswith("/v1/") for p in schema["paths"])
        c.headers["Authorization"] = "Bearer " + two["token"]
        assert c.get("/v1/jobs/" + task["id"]).status_code == 403
        assert c.post("/v1/jobs/" + task["id"] + "/cancel", json={}).status_code == 403
        c.headers.pop("Authorization")
        assert c.post("/api/session", json=credentials).status_code == 200
        c.post("/api/api-keys/" + one["id"] + "/revoke", json={})
        c.headers["Authorization"] = "Bearer " + one["token"]
        assert c.get("/v1/jobs/" + task["id"]).status_code == 401
        c.headers.pop("Authorization")
        alice = {"username": "catalog-alice", "password": "Synthetic password 123!"}
        assert c.post("/api/account/register", json=alice).status_code == 200
        assert c.post("/api/session", json=alice).status_code == 200
        assert c.get("/api/applications/" + first["id"]).status_code == 404
        assert c.get("/v1/jobs/" + task["id"]).status_code == 404
        assert c.post("/v1/apps/identity-first/recognitions", json={"file_ids": [source]}).status_code == 404
        c.headers["X-Tenant-ID"] = team
        assert c.get("/api/applications").status_code == 403
