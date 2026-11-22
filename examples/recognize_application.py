"""Call a published application without knowing its prompts or model connection.

The API credential is read from the environment and never printed. This client
uploads only the explicitly named local file, polls a durable job and writes the
public result, preserving partial/failed status instead of substituting answers.
"""

import argparse
import json
import os
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--app", required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8767")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()
    token = os.environ.get("WORKBENCH_ACCESS_TOKEN")
    if not token:
        parser.exit(2, "Set WORKBENCH_ACCESS_TOKEN first.\n")
    try:
        with httpx.Client(
            base_url=args.url, headers={"Authorization": "Bearer " + token}, timeout=30, trust_env=False
        ) as client:
            with args.file.open("rb") as stream:
                uploaded = client.post("/v1/files", files={"file": (args.file.name, stream)})
            uploaded.raise_for_status()
            response = client.post(
                "/v1/apps/" + args.app + "/recognitions", json={"file_ids": [uploaded.json()["id"]]}
            )
            response.raise_for_status()
            task = response.json()
            deadline = time.monotonic() + args.timeout
            while task["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.5)
                response = client.get("/v1/jobs/" + task["id"])
                response.raise_for_status()
                task = response.json()
            text = json.dumps(task, ensure_ascii=False, indent=2)
            if args.output:
                args.output.write_text(text + "\n", encoding="utf-8")
            else:
                print(text)
            if task["status"] != "succeeded":
                parser.exit(1, "Task is incomplete or failed; preserve the returned ID to query it later.\n")
    except httpx.HTTPStatusError as error:
        parser.exit(1, f"API request failed: HTTP {error.response.status_code}.\n")
    except (OSError, httpx.HTTPError, ValueError, KeyError):
        parser.exit(2, "Check the file, API address, response and credential.\n")


if __name__ == "__main__":
    main()
