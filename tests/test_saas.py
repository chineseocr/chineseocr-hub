"""Tenant-boundary and account lifecycle checks, using synthetic data only."""

import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from workbench.saas import create_saas_app

PASSWORD = "Synthetic account password 123!"
MODEL_BASE = "http://127.0.0.1:19999/v1"


@pytest.fixture
def online(tmp_path):
    app = create_saas_app(tmp_path, start_worker=False, registration=True, allowed_model_bases=[MODEL_BASE])
    with TestClient(app) as client:
        client.headers["X-Workbench"] = "1"
        credentials = json.loads((tmp_path / "saas/initial-admin.json").read_text())
        assert client.post("/api/session", json=credentials).status_code == 200
        yield client, app, credentials, tmp_path


def login(client, credentials):
    client.headers.pop("Authorization", None)
    client.headers.pop("X-Tenant-ID", None)
    assert client.post("/api/session", json=credentials).status_code == 200
    return client.get("/api/account").json()


def register(client, name):
    body = {"username": name, "password": PASSWORD}
    assert client.post("/api/account/register", json=body).status_code == 200
    return body


def upload(client, filename="synthetic.png"):
    image = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(image, "PNG")
    response = client.post("/api/files", files={"file": (filename, image.getvalue())})
    assert response.status_code == 200
    return response.json()


def provider(client):
    response = client.post(
        "/api/providers",
        json={
            "name": "Synthetic protocol target",
            "base_url": MODEL_BASE,
            "model": "vision",
            "api_key": "synthetic-private-model-key",
        },
    )
    assert response.status_code == 200
    return response.json()


def job_body(client, source, model):
    template = client.get("/api/bootstrap").json()["templates"][0]
    return {
        "template_id": template["id"],
        "version": template["version"],
        "provider_id": model["id"],
        "file_ids": [source["id"]],
    }


def test_tenants_cannot_read_each_others_resources_or_spoof_ids(online):
    client, app, admin, root = online
    admin_team = client.get("/api/account").json()["active_tenant_id"]
    source = upload(client)
    model = provider(client)
    task = client.post("/api/jobs", json=job_body(client, source, model)).json()
    dataset = client.post("/api/datasets", json={"name": "Private validation"}).json()
    template = client.get("/api/bootstrap").json()["templates"][0]
    key = client.post("/api/api-keys", json={"name": "Tenant-bound"}).json()
    alice = register(client, "alice")
    alice_team = login(client, alice)["active_tenant_id"]
    for path in (
        "/api/files/" + source["id"] + "/download",
        "/api/files/" + source["id"] + "/pages/1",
        "/api/jobs/" + task["id"],
        "/api/datasets/" + dataset["id"],
    ):
        assert client.get(path).status_code == 404
    assert client.post("/api/templates/" + template["id"] + "/versions", json=template).status_code == 404
    client.headers["X-Tenant-ID"] = admin_team
    assert client.get("/api/bootstrap").status_code == 403
    client.headers.pop("X-Tenant-ID")
    login(client, admin)
    client.headers["X-Tenant-ID"] = alice_team
    assert client.get("/api/bootstrap").status_code == 403  # Even platform admins need membership.
    client.headers["Authorization"] = "Bearer " + key["token"]
    assert client.get("/api/bootstrap").status_code == 403
    client.headers["X-Tenant-ID"] = admin_team
    assert client.get("/api/bootstrap").status_code == 200
    assert client.get("/api/admin/tenants").status_code == 403
    assert (
        client.post(
            "/api/providers", json={"name": "Forbidden", "base_url": MODEL_BASE, "model": "vision"}
        ).status_code
        == 403
    )
    raw = (root / "saas/platform.sqlite3").read_bytes()
    assert key["token"].encode() not in raw


def test_invites_roles_removal_and_scoped_key_revocation(online):
    client, app, admin, root = online
    team = client.get("/api/account").json()["active_tenant_id"]
    invite = client.post("/api/invitations", json={"role": "viewer"}).json()
    alice = register(client, "alice")
    account = login(client, alice)
    alice_id = account["user"]["id"]
    assert client.post("/api/invitations/accept", json={"token": invite["token"]}).status_code == 200
    assert client.post("/api/invitations/accept", json={"token": invite["token"]}).status_code == 404
    assert client.post("/api/account/active", json={"tenant_id": team}).status_code == 200
    assert client.get("/api/bootstrap").json()["saas"]["role"] == "viewer"
    assert client.post("/api/dictionaries", json={"name": "Denied", "values": ["a"]}).status_code == 403
    assert client.post("/api/api-keys", json={"name": "Escalation", "role": "member"}).status_code == 403
    assert (
        client.post("/api/tenants/members/" + alice_id + "/role", json={"role": "admin"}).status_code == 403
    )
    key = client.post("/api/api-keys", json={"name": "Read-only", "role": "viewer"}).json()
    login(client, admin)
    assert client.post("/api/tenants/members/" + alice_id + "/remove", json={}).status_code == 200
    client.headers["Authorization"] = "Bearer " + key["token"]
    assert client.get("/api/bootstrap").status_code == 401  # Revoked at removal.
    assert not app.state.accounts.one(
        "SELECT role FROM memberships WHERE tenant_id=? AND user_id=?", (team, alice_id)
    )


def test_model_allowlist_encryption_and_no_operator_profile_inheritance(online, monkeypatch):
    client, app, admin, root = online
    called = []
    monkeypatch.setattr("workbench.app.discover_models", lambda *args: called.append(args))
    denied = {"name": "Private network", "base_url": "http://169.254.169.254/v1", "model": "vision"}
    assert client.post("/api/providers", json=denied).status_code == 403
    assert client.post("/api/providers/discover", json=denied).status_code == 403
    assert called == []
    saved = provider(client)
    body = client.get("/api/bootstrap").text
    assert "synthetic-private-model-key" not in body and "api_key_encrypted" not in body
    tenant = client.get("/api/account").json()["active_tenant_id"]
    store = app.state.runtime.apps[tenant].state.store
    persisted = store.one("SELECT body FROM providers WHERE id=?", (saved["id"],))["body"]
    assert "synthetic-private-model-key" not in persisted
    assert "api_key_encrypted" in persisted
    assert store.get("providers", saved["id"])["api_key"] == "synthetic-private-model-key"
    monkeypatch.setenv("WORKBENCH_MODEL_BASE_URL", MODEL_BASE)
    monkeypatch.setenv("WORKBENCH_MODEL_NAME", "operator-private")
    monkeypatch.setenv("WORKBENCH_MODEL_API_KEY", "operator-secret")
    new = client.post("/api/tenants", json={"name": "Isolated"}).json()
    client.post("/api/account/active", json={"tenant_id": new["id"]})
    assert client.get("/api/bootstrap").json()["providers"] == []


def test_quota_admission_is_atomic_idempotent_and_includes_retries(online):
    client, app, admin, root = online
    tenant = client.get("/api/account").json()["active_tenant_id"]
    limits = {"plan": "custom", "monthly_pages": 1, "storage_bytes": 1024 * 1024, "members": 5}
    assert client.post("/api/admin/tenants/" + tenant + "/limits", json=limits).status_code == 200
    model, source = provider(client), upload(client)
    body = job_body(client, source, model)
    first = client.post("/api/jobs", json=body, headers={"Idempotency-Key": "stable"}).json()
    same = client.post("/api/jobs", json=body, headers={"Idempotency-Key": "stable"}).json()
    assert first["id"] == same["id"]
    assert client.post("/api/jobs", json=body).status_code == 422
    usage = client.get("/api/usage").json()["usage"]
    assert usage["admitted_pages"] == usage["jobs"] == 1
    store = app.state.runtime.apps[tenant].state.store
    store.execute("UPDATE jobs SET status='failed' WHERE id=?", (first["id"],))
    assert client.post("/api/jobs/" + first["id"] + "/retry", json={}).status_code == 422
    assert client.get("/api/usage").json()["usage"]["jobs"] == 1
    assert client.get("/api/usage").json()["usage"]["prompt_tokens"] == 0


def test_batch_quota_failure_rolls_back_all_jobs_and_reservations(online):
    client, app, admin, root = online
    tenant = client.get("/api/account").json()["active_tenant_id"]
    client.post(
        "/api/admin/tenants/" + tenant + "/limits",
        json={"plan": "custom", "monthly_pages": 1, "storage_bytes": 1024 * 1024, "members": 5},
    )
    model, source = provider(client), upload(client)
    dataset = client.post("/api/datasets", json={"name": "Two samples"}).json()
    for _ in range(2):
        client.post(
            "/api/datasets/" + dataset["id"] + "/samples",
            json={"file_id": source["id"], "gold": {"total": 1}},
        )
    template = client.get("/api/bootstrap").json()["templates"][0]
    response = client.post(
        "/api/datasets/" + dataset["id"] + "/runs",
        json={"template_id": template["id"], "provider_id": model["id"]},
    )
    assert response.status_code == 422
    assert client.get("/api/jobs").json() == client.get("/api/runs").json() == []
    assert client.get("/api/usage").json()["usage"]["admitted_pages"] == 0


def test_logout_and_password_change_revoke_server_credentials(online):
    client, app, admin, root = online
    old_cookie = client.cookies["studio_session"]
    key = client.post("/api/api-keys", json={"name": "Before password change"}).json()
    assert (
        client.post(
            "/api/account/password", json={"current_password": admin["password"], "new_password": PASSWORD}
        ).status_code
        == 200
    )
    assert client.get("/api/account").status_code == 401
    client.cookies.set("studio_session", old_cookie)
    assert client.get("/api/bootstrap").status_code == 401
    client.headers["Authorization"] = "Bearer " + key["token"]
    assert client.get("/api/bootstrap").status_code == 401
    client.cookies.clear()
    login(client, {"username": "admin", "password": PASSWORD})
    cookie = client.cookies["studio_session"]
    assert client.delete("/api/session").status_code == 200
    client.cookies.set("studio_session", cookie)
    assert client.get("/api/bootstrap").status_code == 401


def test_saas_csrf_inputs_and_invite_only_registration(tmp_path):
    app = create_saas_app(tmp_path, start_worker=False)
    with TestClient(app) as client:
        credentials = json.loads((tmp_path / "saas/initial-admin.json").read_text())
        assert client.post("/api/session", json=credentials).status_code == 403
        client.headers["X-Workbench"] = "1"
        assert (
            client.post("/api/session", json=credentials, headers={"Origin": "https://evil.test"}).status_code
            == 403
        )
        assert (
            client.post(
                "/api/account/register", json={"username": "closed", "password": PASSWORD}
            ).status_code
            == 403
        )
        response = client.post(
            "/api/session", json={"username": "bad", "password": "private-value", "extra": True}
        )
        assert "private-value" not in response.text
        assert client.get("/healthz").json()["mode"] == "saas"


def test_saas_restart_preserves_account_membership_and_queues(tmp_path):
    credentials = None
    for attempt in range(2):
        with TestClient(create_saas_app(tmp_path, start_worker=False)) as client:
            client.headers["X-Workbench"] = "1"
            credentials = credentials or json.loads((tmp_path / "saas/initial-admin.json").read_text())
            login(client, credentials)
            data = client.get("/api/bootstrap").json()
            if not attempt:
                assert (
                    client.post("/api/dictionaries", json={"name": "Persistent", "values": ["A"]}).status_code
                    == 200
                )
            else:
                assert data["dictionaries"][0]["name"] == "Persistent"
                assert len(client.get("/api/account").json()["tenants"]) == 1


def test_storage_quota_rejects_upload_and_removes_partial_files(online):
    client, app, admin, root = online
    team = client.get("/api/account").json()["active_tenant_id"]
    assert (
        client.post(
            "/api/admin/tenants/" + team + "/limits",
            json={"plan": "custom", "monthly_pages": 100, "storage_bytes": 1024, "members": 5},
        ).status_code
        == 200
    )
    image = io.BytesIO()
    Image.effect_noise((128, 128), 100).convert("RGB").save(image, "PNG")
    assert client.post("/api/files", files={"file": ("synthetic.png", image.getvalue())}).status_code == 422
    store = app.state.runtime.apps[team].state.store
    assert store.all("SELECT * FROM files") == []
    assert list((store.directory / "files").iterdir()) == []


def test_expired_sessions_and_disabled_accounts_are_denied(online):
    client, app, admin, root = online
    alice = register(client, "alice")
    user = login(client, alice)["user"]
    key = client.post("/api/api-keys", json={"name": "Disabled account"}).json()["token"]
    with app.state.accounts.connect() as db:
        db.execute("UPDATE sessions SET expires=0 WHERE user_id=?", (user["id"],))
    assert client.get("/api/bootstrap").status_code == 401
    login(client, admin)
    assert (
        client.post("/api/admin/users/" + user["id"] + "/status", json={"disabled": True}).status_code == 200
    )
    assert client.post("/api/session", json=alice).status_code == 401
    client.headers["Authorization"] = "Bearer " + key
    assert client.get("/api/bootstrap").status_code == 401


def test_removed_final_membership_can_recover_through_account_routes(online):
    client, app, admin, root = online
    invite = client.post("/api/invitations", json={"role": "member"}).json()["token"]
    credentials = {"username": "invited-only", "password": PASSWORD, "invitation": invite}
    assert client.post("/api/account/register", json=credentials).status_code == 200
    invited = login(client, {k: v for k, v in credentials.items() if k != "invitation"})
    user = invited["user"]["id"]
    cookie = client.cookies["studio_session"]
    login(client, admin)
    assert client.post("/api/tenants/members/" + user + "/remove", json={}).status_code == 200
    client.cookies.clear()
    client.cookies.set("studio_session", cookie)
    account = client.get("/api/account").json()
    assert account["tenants"] == [] and account["active_tenant_id"] is None
    assert client.get("/api/bootstrap").status_code == 403
    own = client.post("/api/tenants", json={"name": "Recovered workspace"}).json()
    assert client.post("/api/account/active", json={"tenant_id": own["id"]}).status_code == 200
    assert client.get("/api/bootstrap").status_code == 200


def test_member_capacity_reserves_pending_invitations_and_owner_is_immutable(online):
    client, app, admin, root = online
    me = client.get("/api/account").json()
    team = me["active_tenant_id"]
    client.post(
        "/api/admin/tenants/" + team + "/limits",
        json={"plan": "custom", "monthly_pages": 100, "storage_bytes": 1000000, "members": 2},
    )
    invite = client.post("/api/invitations", json={"role": "member"}).json()
    assert client.post("/api/invitations", json={"role": "member"}).status_code == 422
    assert client.post("/api/invitations/" + invite["id"] + "/revoke", json={}).status_code == 200
    assert client.post("/api/invitations", json={"role": "member"}).status_code == 200
    assert (
        client.post("/api/tenants/members/" + me["user"]["id"] + "/role", json={"role": "viewer"}).status_code
        == 403
    )
    assert client.post("/api/tenants/members/" + me["user"]["id"] + "/remove", json={}).status_code == 403
    events = client.get("/api/audit").json()
    assert any(event["event"] == "invitation.revoked" for event in events)


def test_lost_encryption_key_fails_closed_and_releases_platform_lock(tmp_path):
    with TestClient(create_saas_app(tmp_path, start_worker=False)):
        pass
    key = tmp_path / "saas/provider-encryption.key"
    original = key.read_bytes()
    key.unlink()
    with pytest.raises(RuntimeError, match="加密密钥"):
        with TestClient(create_saas_app(tmp_path, start_worker=False)):
            pass
    key.write_bytes(original)
    with TestClient(create_saas_app(tmp_path, start_worker=False)) as client:
        assert client.get("/healthz").status_code == 200


def test_page_admissions_serialize_concurrent_submissions(online):
    from concurrent.futures import ThreadPoolExecutor

    from workbench.domain import JobInput
    from workbench.engine import create_job

    client, app, admin, root = online
    me = client.get("/api/account").json()
    team = me["active_tenant_id"]
    client.post(
        "/api/admin/tenants/" + team + "/limits",
        json={"plan": "custom", "monthly_pages": 1, "storage_bytes": 1000000, "members": 5},
    )
    model, source = provider(client), upload(client)
    # Freeze one legitimate payload; submit it concurrently through the same Store
    # transaction used by HTTP routes, without sharing mutable HTTP client cookies.
    request = JobInput(**job_body(client, source, model))
    store = app.state.runtime.apps[team].state.store
    store.execute("DELETE FROM jobs")
    store.execute("DELETE FROM usage_admissions")

    def submit(_):
        try:
            return create_job(store, request)
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(submit, range(4)))
    assert sum(result is not None for result in results) == 1
    assert client.get("/api/usage").json()["usage"]["admitted_pages"] == 1


def test_recognition_jobs_crops_and_exports_follow_tenant_roles_and_quotas(online, monkeypatch):
    from workbench.adapters import BACKENDS
    from workbench.engine import Worker

    client, app, admin, root = online
    team = client.get("/api/account").json()["active_tenant_id"]
    source, model = upload(client), provider(client)

    class Vision:
        def __init__(self):
            self.n = 0

        def extract(self, *args):
            self.n += 1
            data = (
                {"regions": [{"label": "invoice", "box": [0, 0, 1000, 1000]}]}
                if self.n == 1
                else {"text": "Tenant private synthetic", "blocks": []}
            )
            return {"raw_text": json.dumps(data), "usage": {"prompt_tokens": 2, "completion_tokens": 1}}

    monkeypatch.setitem(BACKENDS, "openai", Vision())
    body = {"task": "documents", "provider_id": model["id"], "file_ids": [source["id"]]}
    task = client.post("/api/recognition/jobs", json=body).json()
    Worker(app.state.runtime.apps[team].state.store).process(task["id"])
    result = client.get("/api/jobs/" + task["id"]).json()
    instance = result["results"][0]["instances"][0]["id"]
    paths = [
        "/api/jobs/" + task["id"],
        "/api/jobs/" + task["id"] + "/export",
        "/api/jobs/" + task["id"] + "/regions/" + instance + "/crop",
    ]
    assert all(client.get(path).status_code == 200 for path in paths)
    alice = register(client, "recognition-alice")
    login(client, alice)
    assert all(client.get(path).status_code == 404 for path in paths)
    assert client.post("/api/recognition/jobs", json=body).status_code == 422
    login(client, admin)
    invite = client.post("/api/invitations", json={"role": "viewer"}).json()["token"]
    login(client, alice)
    client.post("/api/invitations/accept", json={"token": invite})
    client.post("/api/account/active", json={"tenant_id": team})
    assert client.get(paths[2]).status_code == 200
    assert client.post("/api/recognition/jobs", json=body).status_code == 403
    login(client, admin)
    client.post(
        "/api/admin/tenants/" + team + "/limits",
        json={"plan": "custom", "monthly_pages": 1, "storage_bytes": 1000000, "members": 5},
    )
    assert client.get("/api/usage").json()["usage"]["admitted_pages"] == 1
    assert client.post("/api/recognition/jobs", json=body).status_code == 422


def test_members_edit_drafts_but_only_admins_publish_applications(online):
    client, app, admin, root = online
    team = client.get("/api/account").json()["active_tenant_id"]
    entry = client.get("/api/applications").json()["items"][0]
    model = provider(client)
    invite = client.post("/api/invitations", json={"role": "member"}).json()
    bob = register(client, "application-editor")
    login(client, bob)
    assert client.post("/api/invitations/accept", json={"token": invite["token"]}).status_code == 200
    assert client.post("/api/account/active", json={"tenant_id": team}).status_code == 200
    assert (
        client.post(
            "/api/applications/" + entry["id"] + "/metadata",
            json={"code": entry["code"], "description": "Member draft"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/applications/" + entry["id"] + "/publish", json={"version": 1, "provider_id": model["id"]}
        ).status_code
        == 403
    )
    assert (
        client.post("/api/applications/" + entry["id"] + "/state", json={"disabled": True}).status_code == 403
    )
    key = client.post("/api/api-keys", json={"name": "Editor key"}).json()
    client.headers["Authorization"] = "Bearer " + key["token"]
    assert client.post("/api/applications/install-presets", json={}).status_code == 403
