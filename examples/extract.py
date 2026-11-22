"""python examples/extract.py image.png [--provider-id ID] [--mode page]"""

import argparse
import json
import os
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description="Submit and poll an extraction job")
    parser.add_argument("file", type=Path)
    parser.add_argument("--provider-id", help="Defaults to the workspace model connection")
    parser.add_argument("--mode", choices=["page", "document"], default="document")
    args = parser.parse_args()
    token = os.environ.get("WORKBENCH_ACCESS_TOKEN") or Path("data/access-token").read_text().strip()
    base = os.environ.get("WORKBENCH_URL", "http://127.0.0.1:8765")
    with httpx.Client(base_url=base, headers={"Authorization": "Bearer " + token}, timeout=120) as client:
        setup = client.get("/api/bootstrap").raise_for_status().json()
        template = setup["templates"][0]
        if not setup["providers"]:
            raise SystemExit("Configure a vision model in Model connections before uploading documents.")
        provider = args.provider_id or setup["default_provider_id"] or setup["providers"][0]["id"]
        with args.file.open("rb") as source:
            file = (
                client.post("/api/files", files={"file": (args.file.name, source)}).raise_for_status().json()
            )
        job = (
            client.post(
                "/api/jobs",
                json={
                    "template_id": template["id"],
                    "version": template["version"],
                    "provider_id": provider,
                    "file_ids": [file["id"]],
                    "mode": args.mode,
                },
            )
            .raise_for_status()
            .json()
        )
        while job["status"] in {"queued", "running"}:
            time.sleep(0.5)
            job = client.get("/api/jobs/" + job["id"]).raise_for_status().json()
        print(json.dumps(job, ensure_ascii=False, indent=2))
        if job["status"] != "succeeded":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
