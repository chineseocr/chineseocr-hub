"""Exercise a running installation/container using only synthetic local input.

No provider is configured and no inference is submitted. WORKBENCH_SMOKE_TOKEN is
an ephemeral test credential, read from the environment and never logged.
"""

import argparse
import io
import json
import os
import time
import urllib.error
import urllib.request
import uuid

from PIL import Image


def smoke(base_url, token, marker=None):
    """Check HTTP, auth, configuration writes and upload; optionally check restart persistence."""

    def request(path, body=None, authorized=True, content_type="application/json"):
        headers = {"Accept-Language": "en"}
        if authorized:
            headers["Authorization"] = "Bearer " + token
        if body is not None:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(base_url.rstrip("/") + path, data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    for _ in range(45):
        try:
            if request("/healthz", authorized=False)[0] == 200:
                break
        except (OSError, TimeoutError):
            pass
        time.sleep(1)
    else:
        raise RuntimeError("Application did not become healthy within 45 seconds")
    assert request("/api/bootstrap", authorized=False)[0] == 401
    for path in ("/", "/startup.js", "/style.css", "/assets/locales/en.json", "/api/openapi.json"):
        assert request(path)[0] == 200, path
    status, raw = request("/api/bootstrap")
    assert status == 200
    data = json.loads(raw)
    assert not data["providers"], "Smoke must use an isolated workspace without a model connection"
    assert request("/api/template-presets")[0] == 200
    if marker:
        assert any(item["name"] == marker for item in data["dictionaries"])
        assert any(item["name"] == marker + ".png" for item in data["files"])
        print("HTTP restart persistence passed: dictionary and document retained.")
        return marker
    marker = "synthetic-smoke-" + uuid.uuid4().hex
    body = json.dumps({"name": marker, "values": ["A", "B", None]}).encode()
    status, saved = request("/api/dictionaries", body)
    assert status == 200 and json.loads(saved)["version"] == 1
    image = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(image, format="PNG")
    boundary = uuid.uuid4().hex
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{marker}.png"\r\nContent-Type: image/png\r\n\r\n'.encode()
        + image.getvalue()
        + f"\r\n--{boundary}--\r\n".encode()
    )
    status, raw = request("/api/files", body, content_type="multipart/form-data; boundary=" + boundary)
    assert status == 200
    file_id = json.loads(raw)["id"]
    assert request("/api/files/" + file_id + "/pages/1")[0] == 200
    assert request("/api/files/" + file_id + "/pages/1", authorized=False)[0] == 401
    print("HTTP smoke passed: auth, assets, presets, dictionary and synthetic upload; no inference.")
    return marker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--marker-file", required=True)
    parser.add_argument("--check-persistence", action="store_true")
    args = parser.parse_args()
    from pathlib import Path

    token = os.environ.get("WORKBENCH_SMOKE_TOKEN", "")
    if len(token) < 16:
        raise SystemExit("Set an ephemeral WORKBENCH_SMOKE_TOKEN (at least 16 characters)")
    marker_file = Path(args.marker_file)
    marker = marker_file.read_text().strip() if args.check_persistence else None
    result = smoke(args.url, token, marker)
    marker_file.write_text(result)


if __name__ == "__main__":
    main()
