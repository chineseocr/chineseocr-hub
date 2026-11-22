"""Exercise an isolated running SaaS installation without calling a model.

Read only generated test administrator credentials; never print credentials,
session cookies or one-time API keys. The marker contains resource IDs only.
"""

import argparse
import io
import json
import time
from pathlib import Path

import httpx
from PIL import Image


def smoke(url, credentials_file, marker_file, persistence=False):
    """Check account/team boundaries and durable resources across a real restart."""
    with httpx.Client(
        base_url=url, headers={"X-Workbench": "1", "Accept-Language": "en"}, timeout=10
    ) as client:
        for _ in range(45):
            try:
                if client.get("/healthz").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("SaaS did not become healthy")
        assert client.get("/api/bootstrap").status_code == 401
        credentials = json.loads(Path(credentials_file).read_text())
        assert client.post("/api/session", json=credentials).status_code == 200
        assert client.get("/api/account/openapi.json").status_code == 200
        if persistence:
            marker = json.loads(Path(marker_file).read_text())
            client.headers["X-Tenant-ID"] = marker["team"]
            bootstrap = client.get("/api/bootstrap").json()
            assert any(row["id"] == marker["file"] for row in bootstrap["files"])
            assert any(row["name"] == "Synthetic persisted dictionary" for row in bootstrap["dictionaries"])
            assert not bootstrap["providers"]
            print("SaaS HTTP restart persistence passed: account, membership, dictionary and synthetic file.")
            return
        account = client.get("/api/account").json()
        original = account["active_tenant_id"]
        created = client.post("/api/tenants", json={"name": "Synthetic isolated team"})
        assert created.status_code == 200
        team = created.json()["id"]
        client.headers["X-Tenant-ID"] = team
        data = client.get("/api/bootstrap").json()
        assert not data["files"] and not data["providers"]
        assert (
            client.post(
                "/api/dictionaries", json={"name": "Synthetic persisted dictionary", "values": ["A", None]}
            ).status_code
            == 200
        )
        image = io.BytesIO()
        Image.new("RGB", (64, 64), "white").save(image, "PNG")
        uploaded = client.post("/api/files", files={"file": ("synthetic.png", image.getvalue(), "image/png")})
        assert uploaded.status_code == 200
        file = uploaded.json()["id"]
        key = client.post("/api/api-keys", json={"name": "Disposable scoped check", "role": "viewer"}).json()
        with httpx.Client(
            base_url=url, headers={"Authorization": "Bearer " + key["token"]}, timeout=10
        ) as scoped:
            assert scoped.get("/api/files/" + file + "/pages/1").status_code == 200
            assert scoped.get("/api/bootstrap", headers={"X-Tenant-ID": original}).status_code == 403
            assert (
                scoped.post("/api/dictionaries", json={"name": "Denied", "values": ["A"]}).status_code == 403
            )
        assert client.post("/api/api-keys/" + key["id"] + "/revoke", json={}).status_code == 200
        client.headers["X-Tenant-ID"] = original
        assert client.get("/api/files/" + file + "/pages/1").status_code == 404
        Path(marker_file).write_text(json.dumps({"team": team, "file": file}))
        print(
            "SaaS HTTP smoke passed: login, team isolation, scoped permissions, dictionary and upload; no inference."
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8767")
    parser.add_argument("--credentials-file", required=True, type=Path)
    parser.add_argument("--marker-file", required=True, type=Path)
    parser.add_argument("--check-persistence", action="store_true")
    args = parser.parse_args()
    smoke(args.url, args.credentials_file, args.marker_file, args.check_persistence)


if __name__ == "__main__":
    main()
