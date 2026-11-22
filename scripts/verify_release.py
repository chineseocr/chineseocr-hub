"""Verify a source archive before it is copied into a public repository.

Checks the allowlist, byte-for-byte source parity, checksum and optional known private
workspace values. Extraction/smoke tests happen only after every member is checked.
This is a release guard, not an assertion of complete security or license clearance.
"""

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.build_release import release_files  # noqa: E402


def private_values(directory):
    """Read known tokens/keys/remote hosts without printing or copying them into reports."""
    folder = Path(directory).resolve()
    values = []
    token = folder / "access-token"
    if token.is_file():
        values.append(token.read_bytes().strip())
    # Hosted master keys and bootstrap credentials must remain outside any release.
    master = folder / "saas/provider-encryption.key"
    if master.is_file():
        values.append(master.read_bytes().strip())
    initial = folder / "saas/initial-admin.json"
    if initial.is_file():
        values.append(json.loads(initial.read_text())["password"].encode())
    database = folder / "workbench.sqlite3"
    if database.is_file():
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
            for (body,) in db.execute("SELECT body FROM providers"):
                provider = json.loads(body)
                if provider.get("api_key"):
                    values.append(provider["api_key"].encode())
                host = urlsplit(provider.get("base_url", "")).hostname or ""
                if host and host not in {"localhost", "127.0.0.1", "::1"} and not host.endswith(".test"):
                    values.append(host.encode())
    return [value for value in values if value]


def verify_archive(archive, root=ROOT, private_directory=None):
    """Reject extra files, unsafe paths, secrets and stale files before any code is run."""
    archive, root = Path(archive), Path(root)
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    expected_hash = checksum.read_text().split()[0]
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected_hash:
        raise ValueError("Release checksum does not match")
    expected = {str(file.relative_to(root)): file.read_bytes() for file in release_files(root)}
    secrets = private_values(private_directory) if private_directory else []
    actual, prefixes = {}, set()
    with tarfile.open(archive) as package:
        for member in package.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not member.isfile() or len(path.parts) < 2:
                raise ValueError("Unsafe release member: " + member.name)
            prefixes.add(path.parts[0])
            relative = str(PurePosixPath(*path.parts[1:]))
            if relative in actual or relative not in expected or member.size > 5 * 1024 * 1024:
                raise ValueError("Unexpected or duplicate release member: " + relative)
            content = package.extractfile(member).read()
            if any(secret in content for secret in secrets):
                raise ValueError("Known private value found in: " + relative)
            if content != expected[relative]:
                raise ValueError("Release differs from current source: " + relative)
            actual[relative] = content
    if len(prefixes) != 1 or actual.keys() != expected.keys():
        raise ValueError("Incomplete or mixed-root release archive")
    return len(actual)


SMOKE = """
import io
from pathlib import Path
from fastapi.testclient import TestClient
from PIL import Image
from workbench.app import create_app

with TestClient(create_app(Path("clean-workspace"))) as client:
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/bootstrap").status_code == 401
    client.headers["Authorization"] = "Bearer " + client.app.state.store.token
    data = client.get("/api/bootstrap").json()
    assert len(data["templates"]) == 1
    assert not data["files"] and not data["providers"] and not data["datasets"]
    assert client.get("/assets/locales/en.json").status_code == 200
    assert client.get("/startup.js").status_code == 200
    assert client.get("/style.css").status_code == 200
    assert client.get("/api/openapi.json").status_code == 200
    assert client.get("/api/recognition/capabilities").json()["engine"] == "multimodal"
    assert client.get("/assets/recognition.js").status_code == 200
    assert client.get("/assets/applications.js").status_code == 200
    assert len(client.get("/api/application-presets").json()) == 17
    assert client.get("/api/applications").json()["total"] == 1
    presets = client.get("/api/template-presets", headers={"Accept-Language": "en"}).json()
    assert any(p["id"] == "identity-card" for p in presets)
    stream = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(stream, format="PNG")
    uploaded = client.post("/api/files", files={"file": ("synthetic.png", stream.getvalue())})
    assert uploaded.status_code == 200
    assert client.get("/api/files/" + uploaded.json()["id"] + "/pages/1").status_code == 200
from workbench.saas import create_saas_app
import json
folder = Path("clean-saas")
with TestClient(create_saas_app(folder, start_worker=False)) as client:
    client.headers["X-Workbench"] = "1"
    credentials = json.loads((folder / "saas/initial-admin.json").read_text())
    assert client.get("/api/bootstrap").status_code == 401
    assert client.post("/api/session", json=credentials).status_code == 200
    assert client.get("/api/bootstrap").json()["saas"]["role"] == "owner"
    assert client.get("/assets/tenants.js").status_code == 200
print("Extracted source smoke: community and SaaS startup, authentication, assets, presets and upload passed; no inference called.")
"""


def smoke_archive(archive):
    """Run the verified archive in a disposable directory with deployment secrets removed."""
    with tempfile.TemporaryDirectory(prefix="source-release-smoke-") as directory:
        with tarfile.open(archive) as package:
            package.extractall(directory, filter="data")
        folder = next(Path(directory).iterdir())
        environment = {key: value for key, value in os.environ.items() if not key.startswith("WORKBENCH_")}
        environment["PYTHONPATH"] = str(folder)
        result = subprocess.run(
            [sys.executable, "-c", SMOKE],
            cwd=folder,
            env=environment,
            text=True,
            capture_output=True,
            timeout=60,
        )
        if result.returncode:
            # Do not emit arbitrary subprocess logs: a failure could include a secret.
            raise ValueError("Extracted-source smoke failed; inspect the disposable run locally")
        print(result.stdout.strip())


def main():
    """Validate by default; --smoke additionally needs the development/test dependencies."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--private-workspace", type=Path)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    count = verify_archive(args.archive, private_directory=args.private_workspace)
    print(f"Release check passed: {count} allowlisted files; checksum and source parity verified.")
    if args.smoke:
        smoke_archive(args.archive)


if __name__ == "__main__":
    main()
