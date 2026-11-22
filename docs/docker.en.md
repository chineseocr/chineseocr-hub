# Docker deployment and operations

[中文](docker.md) · [User guide](user-guide.en.md) · [SaaS permissions](saas.md)

For **v0.6.0a1 Alpha**. The container hosts the workbench, not the multimodal model. No GPU runtime or weights are bundled. Build from the supplied Dockerfile/Compose files; no official registry image has been published.

## 1. Requirements

Use Docker Engine with `host-gateway` support (20.10+) and Compose v2, or Docker Desktop. Run the commands from the project root containing `Dockerfile`, `compose.yaml` and `requirements.lock`. On Windows, use WSL2 Bash for these examples.

```bash
docker version
docker compose version
```

The service is initially limited to 2 CPUs and 1GiB RAM. These are configuration defaults, not a capacity guarantee. Adjust after testing your PDFs and team workload. Building requires network access to the base image and locked dependencies.

## 2. Start community mode

Copy the example only if `.env` does not exist. Preserve existing private settings on an installed deployment.

```bash
cp .env.example .env
chmod 600 .env
docker compose config --quiet
docker compose build --pull
docker compose up -d
docker compose ps
docker compose exec workbench cat /app/data/access-token
```

Open [http://127.0.0.1:8765/?lang=en](http://127.0.0.1:8765/?lang=en) and enter the generated token. If you set `WORKBENCH_TOKEN` (at least 16 characters), that environment value takes precedence over the token file. Read credentials only in a private terminal.

Community mode is one trusted workspace with a shared administrative token. Data persists in a named volume. Your source tree's `data/` directory is not mounted or imported automatically. Stop a local Python deployment before migrating its complete data directory.

## 3. Start multi-tenant mode

Before the first startup, edit `.env`:

```dotenv
WORKBENCH_MODE=saas
WORKBENCH_ALLOW_REGISTRATION=0
WORKBENCH_ALLOWED_MODEL_BASES=https://vision.example.test/v1
```

Replace the example with an approved real endpoint. An empty allowlist rejects all model destinations. Build and start as above, then privately read the initial account:

```bash
docker compose exec workbench cat /app/data/saas/initial-admin.json
```

Sign in, change the password under **Team & usage**, then delete the bootstrap file:

```bash
docker compose exec workbench python -c "from pathlib import Path; Path('/app/data/saas/initial-admin.json').unlink(missing_ok=True)"
```

Registration is invitation-only by default; email is not sent. Team administrators save model connections within their own teams. Operator `WORKBENCH_MODEL_*` profiles initialize community mode only and are not inherited by tenants. Switching modes does not migrate community documents. See [SaaS operations](saas.md) for roles, keys and quotas.

## 4. Connect a multimodal service

See **[self-hosted Qwen-VL / OpenAI API](model-connections.en.md)** for complete examples. Optional `compose.qwen.yaml` adds a Linux/NVIDIA model service; retain both `-f` arguments for subsequent operations.

Use **Model connections** to save a Base URL, API key and discovered real model ID. The endpoint must accept Chat Completions with PNG `image_url` data URLs. Model discovery alone does not establish visual capabilities; run a synthetic sample.

Community mode can also initialize a connection from `.env`:

```dotenv
WORKBENCH_MODEL_BASE_URL=https://vision.example.test/v1
WORKBENCH_MODEL_NAME=your-vision-model
WORKBENCH_MODEL_API_KEY=your-private-key
WORKBENCH_MODEL_REQUEST_PROFILE=compatible
WORKBENCH_MODEL_LABEL=My vision service
```

After changing environment settings, use `docker compose up -d --force-recreate`; `restart` does not apply a new environment. Use `config --quiet` for validation because full `config` output may expose credentials.

| Model location | Address and requirements |
|---|---|
| Remote | Your actual HTTPS API base, such as `https://vision.example.test/v1` |
| Docker host | `http://host.docker.internal:8000/v1`; Compose supplies the host-gateway mapping |
| Another container | `http://model-service:8000/v1`; explicitly attach both services to one network, as the supplied Compose does not run a model |

Container loopback is not host loopback. On Linux, a host model must listen on a bridge-reachable interface and permit the traffic through its firewall. A DNS mapping cannot make a loopback-only listener reachable. Do not expose an unauthenticated model publicly. In SaaS, allow the same normalized API base that teams will configure.

## 5. Configuration and daily commands

| Variable | Default / meaning |
|---|---|
| `WORKBENCH_IMAGE_TAG` | `0.6.0a1`, the locally built tag; changing it does not change source or download a release |
| `WORKBENCH_PYTHON_IMAGE` | `python:3.12-slim-bookworm`; replace with a tested digest reference when preparing a release |
| `WORKBENCH_PORT` | Host loopback port `8765`; the internal port stays 8765 |
| `WORKBENCH_MODE` | `community` or `saas` |
| `WORKBENCH_TOKEN` | Community administrative token; SaaS uses accounts |
| `WORKBENCH_ALLOW_REGISTRATION` | `0`, invitation-only SaaS |
| `WORKBENCH_ALLOWED_MODEL_BASES` | Exact comma-separated SaaS destinations; blank denies all |
| `WORKBENCH_MODEL_REQUEST_PROFILE` | `compatible` sends max_tokens/temperature; use `openai` for modern OpenAI parameters |
| `WORKBENCH_MODEL_*` | Optional community model initialization only |

Container storage is fixed at `/app/data`. `WORKBENCH_DATA_DIR` in the example file is for local Python, not a Compose mount override. Named volumes normally include the Compose project prefix. Keep the same project name when upgrading; changing directory names or `-p` can select a different volume.

```bash
curl --fail http://127.0.0.1:8765/healthz
docker compose logs --tail=100 -f workbench
docker compose exec workbench id
docker compose stop -t 200 workbench
docker compose start workbench
docker compose down
```

Health checks test HTTP liveness, not model readiness. Runtime UID/GID is `10001:10001`. Logs rotate at 10MB with three files. Normal `down` retains the volume; **`down -v` deletes it**. Run one process/container per data volume; do not use multiple workers or `--scale`.

## 6. Back up a stopped deployment

These Bash commands stop the service and use the same image's standard-library Python to stream the entire data volume to the host. The helper does not start the application or call a model. Choose a new filename for each backup to avoid overwriting an older copy.

```bash
umask 077
mkdir -p backups
docker compose stop -t 200 workbench
docker compose run --rm --no-deps -T workbench python -c '
import sys
import tarfile
with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz") as archive:
    archive.add("/app/data", arcname="data")
' > backups/workbench-before-upgrade.tar.gz
```

Confirm success and archive readability, then start the service:

```bash
tar -tzf backups/workbench-before-upgrade.tar.gz > /dev/null
docker compose start workbench
```

Backups include databases, private documents, tokens and the SaaS encryption master key. Store them privately and encrypted. Separately preserve private `.env` settings. Database-only backups are insufficient. `backups/` is Git-ignored and excluded from the Docker build context.

## 7. Restore into a separate empty volume

Only restore your own trusted backups. Stop the original deployment to release its port. Use a fresh project name, here `hub-restore`, to avoid overwriting its volume. If that name already exists, choose another name and use it consistently. On a new host, build the source version matching the backup first.

```bash
docker compose stop -t 200 workbench
docker compose -p hub-restore build
docker compose -p hub-restore run --rm --no-deps -T workbench python -c '
from pathlib import Path
import sys
import tarfile
if any(Path("/app/data").iterdir()):
    raise SystemExit("Refusing to restore into a non-empty data directory")
with tarfile.open(fileobj=sys.stdin.buffer, mode="r|gz") as archive:
    archive.extractall("/app", filter="data")
' < backups/workbench-before-upgrade.tar.gz
docker compose -p hub-restore up -d
```

Verify the original mode/environment, login, files, history and model connections. Test with synthetic input before switching operations. Keep the new project name to keep using the restored volume. An extraction failure may leave partial data; investigate and retry into another empty volume.

## 8. Upgrade and distribute offline

Stop and back up first. Update to the intended source version in the **same project directory/name**, preserving private `.env`. Retain the old source, image and pre-upgrade backup for rollback; do not assume an old program can read upgraded data.

```bash
docker compose build --pull
docker compose up -d
docker compose ps
```

For an offline host of the same architecture, export the locally built image on the connected host, then import it on the target:

```bash
docker save -o chineseocr-hub-0.6.0a1.image.tar chineseocr-hub:0.6.0a1
docker load -i chineseocr-hub-0.6.0a1.image.tar
docker compose up -d --no-build --pull never
```

Ship the matching Compose and `.env.example` files; configure a private `.env` on the target. Images contain neither data volumes nor `.env`. Builds target the current architecture by default. No validated official multi-architecture image is available yet.

## 9. Troubleshooting and ingress

| Symptom | Action |
|---|---|
| Port in use | Change `WORKBENCH_PORT`, recreate and visit the new port |
| UI works, inference fails | Check model ID, vision support, API key and container networking |
| SaaS rejects the model URL | Match the operator allowlist to the team's normalized base, then recreate |
| Environment edit ignored | Use `up -d --force-recreate`, not only `restart` |
| Data appears missing | Check project name, selected mode and mounted volume before initializing anything |
| Workspace lock failure | Stop duplicate processes sharing the volume; do not bypass locking |
| Bind-mount permission failure | A custom host directory must be writable by UID/GID 10001; the default named volume needs no manual ownership setup |
| Build failure | Check base-image/dependency access and architecture; retain lock hashes |

Published ports bind host loopback. For public access, use a host HTTPS reverse proxy with domain routing, trusted forwarding, request/body limits and backups. See [deployment](deployment.md) and [security](../SECURITY.md). Community shared tokens are intended for trusted workspaces.

Configuration follows Docker's [Compose service reference](https://docs.docker.com/reference/compose-file/services/), [volumes](https://docs.docker.com/engine/storage/volumes/) and [one-off run command](https://docs.docker.com/reference/cli/docker/compose/run/). On 2026-11-22, Linux CI passed actual image builds and non-root community/SaaS authentication, upload, isolation and restart-persistence checks. Compose configuration passed in both modes; a complete Compose deployment, real-data backup/restore, other architectures and production capacity were not covered. Docker remains unavailable on the local validation machine. See [validation](validation.md).
