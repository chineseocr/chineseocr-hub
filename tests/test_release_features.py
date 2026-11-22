"""Starter configurations, dictionary snapshots and release-boundary regressions."""

import hashlib
import io
import tarfile
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from scripts.build_release import ROOT_DIRS, ROOT_FILES
from scripts.verify_release import verify_archive
from workbench.app import create_app
from workbench.domain import DictionaryInput, schema_errors, validate_schema
from workbench.store import Store


def test_starters_are_authorized_valid_localized_and_do_not_create_data(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/template-presets").status_code == 401
        client.headers["Authorization"] = "Bearer " + client.app.state.store.token
        before = client.get("/api/bootstrap").json()
        chinese = client.get("/api/template-presets").json()
        english = client.get("/api/template-presets", headers={"Accept-Language": "en"}).json()
        assert len(chinese) == len(english) == 5
        for preset in chinese + english:
            validate_schema(preset["schema"])
            assert "{{schema}}" in preset["user_prompt"]
        identity = next(p for p in english if p["id"] == "identity-card")
        assert identity["name"] == "Identity card extraction"
        assert identity["schema"]["properties"]["gender"]["enum"] == ["男", "女", None]
        created = client.post("/api/templates", json=identity)
        assert created.status_code == 200
        assert created.json()["system_prompt"] == identity["system_prompt"]
        assert len(client.get("/api/bootstrap").json()["templates"]) == len(before["templates"]) + 1
        assert before["files"] == before["providers"] == before["dictionaries"] == []
        for path in ("/startup.js", "/style.css"):
            assert client.get(path).status_code == 200


def test_dictionary_updates_do_not_change_template_or_queued_job(tmp_path):
    with TestClient(create_app(tmp_path, start_worker=False)) as client:
        client.headers["Authorization"] = "Bearer " + client.app.state.store.token
        body = {"name": "Document types", "description": "Allowed values", "values": ["invoice", None]}
        first = client.post("/api/dictionaries", json=body).json()
        template = client.post(
            "/api/templates",
            json={
                "name": "Typed document",
                "schema": {
                    "type": "object",
                    "properties": {"kind": {"type": ["string", "null"], "enum": first["values"]}},
                },
            },
        ).json()
        provider = client.post("/api/providers", json={"name": "Not called", "model": "vision"}).json()
        image = io.BytesIO()
        Image.new("RGB", (20, 20), "white").save(image, "PNG")
        file = client.post("/api/files", files={"file": ("synthetic.png", image.getvalue())}).json()
        job = client.post(
            "/api/jobs",
            json={"template_id": template["id"], "provider_id": provider["id"], "file_ids": [file["id"]]},
        ).json()
        second = client.post(
            "/api/dictionaries/" + first["id"] + "/versions", json=body | {"values": ["receipt", None]}
        ).json()
        assert second["version"] == 2
        history = client.get("/api/dictionaries/" + first["id"] + "/versions").json()
        assert [version["values"] for version in history] == [["receipt", None], ["invoice", None]]
        saved = client.get("/api/templates/" + template["id"] + "/versions").json()[0]
        frozen = client.get("/api/jobs/" + job["id"]).json()["payload"]["template"]
        assert frozen["schema"] == saved["schema"] == template["schema"]
        assert schema_errors(saved["schema"], {"kind": "invoice"}) == []
        assert schema_errors(saved["schema"], {"kind": "receipt"})
        assert client.get("/api/dictionaries/missing/versions").status_code == 404
        client.headers.pop("Authorization")
        assert client.get("/api/dictionaries/" + first["id"] + "/versions").status_code == 401


@pytest.mark.parametrize(
    "values", [[], ["a", "a"], [1, 1.0], [{"a": 1}], [["a"]], [float("nan")], [float("inf")]]
)
def test_dictionary_rejects_ambiguous_or_nonliteral_values(values):
    with pytest.raises(ValueError):
        DictionaryInput(name="Invalid", values=values)


def test_dictionary_distinguishes_boolean_from_number_and_allows_null():
    assert DictionaryInput(name="Values", values=[True, 1, None]).values == [True, 1, None]


def test_concurrent_template_saves_return_each_callers_own_version(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.save_template("application", {"name": "Initial"})
    original = store.template
    barrier = threading.Barrier(2)

    def synchronized_read(key, version=None):
        barrier.wait(timeout=5)
        return original(key, version)

    monkeypatch.setattr(store, "template", synchronized_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        saved = list(pool.map(lambda name: store.save_template("application", {"name": name}), ["A", "B"]))
    assert [item["name"] for item in saved] == ["A", "B"]
    assert {item["version"] for item in saved} == {2, 3}


def test_release_guard_rejects_extra_paths_and_stale_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for name in ROOT_FILES:
        (source / name).write_text("public source\n")
    for name in ROOT_DIRS:
        (source / name).mkdir()
    archive = tmp_path / "source.tar.gz"

    def build(extra=None):
        with tarfile.open(archive, "w:gz") as package:
            for name in ROOT_FILES:
                package.add(source / name, arcname="project/" + name)
            if extra:
                info = tarfile.TarInfo(extra)
                info.size = 6
                package.addfile(info, io.BytesIO(b"secret"))
        archive.with_suffix(".gz.sha256").write_text(hashlib.sha256(archive.read_bytes()).hexdigest())

    build()
    assert verify_archive(archive, source) == len(ROOT_FILES)
    build("project/data/access-token")
    with pytest.raises(ValueError, match="Unexpected"):
        verify_archive(archive, source)
    build("project/../escape")
    with pytest.raises(ValueError, match="Unsafe"):
        verify_archive(archive, source)
    build()
    (source / "README.md").write_text("edited after packaging")
    with pytest.raises(ValueError, match="differs"):
        verify_archive(archive, source)
