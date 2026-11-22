# Connect a model: self-hosted Qwen-VL and OpenAI API

[中文](model-connections.md) · [User guide](user-guide.en.md) · [Docker operations](docker.en.md)

For v0.6 Alpha. The workbench manages files, prompts and results. A separate model service performs recognition.

| Situation | Route |
|---|---|
| No GPU; run a first recognition | Example A: an OpenAI API project and key |
| Operate a private Qwen-VL service | Example B: Linux/NVIDIA model container |
| An operator already supplies a service | Use its actual API base/model ID in the connection form |

## 1. Addresses and credentials

The workbench URL, such as `http://127.0.0.1:8765`, is different from the model API base. Use `https://api.openai.com/v1` or your private model base, not a chat website or a full `/chat/completions` endpoint. The workbench adds `/models` for discovery and `/chat/completions` for inference.

A model API key authorizes the upstream service. Workbench login tokens and application keys authorize this project; they are not interchangeable. Never place model secrets in prompts or URLs. Published workbench applications expose their own [client API](applications.md).

The adapter uses Chat Completions with system/user messages and PNG data URLs. It does not call image generation or implement a Responses-only endpoint.

## 2. Example A: OpenAI API without a GPU

Create a private API key in an authorized project at the [OpenAI API platform](https://platform.openai.com/), and confirm model access and available quota. Start and sign in to the workbench. For SaaS, the operator must allow `https://api.openai.com/v1` through `WORKBENCH_ALLOWED_MODEL_BASES` and recreate the container; an admin/owner then configures a team connection.

Under **Model connections**, fill in:

| Field | Example |
|---|---|
| Connection name | `OpenAI vision` |
| Service address | `https://api.openai.com/v1` |
| Model ID | `gpt-6-luna`, subject to your project's access |
| API key | Your private OpenAI project key |
| Request parameter profile | **OpenAI API · modern parameters** |
| Maximum output tokens | Initially `8192`; supported range 128–16384 |
| Timeout / seconds | Initially `180`; supported range 5–180 |

Enter the intended model ID before discovery. An account's list may also contain text/audio/embedding models. Discovery checks the list, not vision or accuracy. Save and select the connection.

This example uses [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna), which documents image input and Chat Completions support. Check account access and [deprecations](https://developers.openai.com/api/docs/deprecations); confirm the chosen model remains available before deployment.

The `openai` profile sends `max_completion_tokens` and omits `temperature`. `compatible` retains `max_tokens` and `temperature=0` for Qwen/vLLM-compatible services. There is no automatic paid retry with alternate parameters. Old connections default to compatible mode; create a new connection and select it in your application release to change modes.

Output limits may include reasoning tokens, so they are not a guarantee of that many visible business tokens. Increase the budget within the workbench limit or reduce pages if output is truncated. See the [Chat Completions parameter reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).

Community mode can initialize from private `.env` instead:

```dotenv
WORKBENCH_MODE=community
WORKBENCH_MODEL_BASE_URL=https://api.openai.com/v1
WORKBENCH_MODEL_NAME=gpt-6-luna
WORKBENCH_MODEL_API_KEY=replace-with-your-private-api-key
WORKBENCH_MODEL_REQUEST_PROFILE=openai
WORKBENCH_MODEL_LABEL=OpenAI vision
```

```bash
docker compose up -d --build --force-recreate
```

Environment initialization uses 4096 output tokens and a 90-second timeout. Create a connection in the UI for the table's larger limits. Existing jobs keep their original connection. SaaS teams never inherit operator `WORKBENCH_MODEL_*` profiles.

## 3. Example B: deploy Qwen-VL privately

The fixed example is **`Qwen/Qwen3-VL-8B-Instruct` with `vllm/vllm-openai:v0.34.0`**. Use a visual model; changing to text-only, Thinking or quantized checkpoints can require different configuration.

An operator must prepare Linux, compatible NVIDIA drivers, Docker/Compose v2 and the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html). Check host `nvidia-smi`, then verify GPU access inside containers using NVIDIA's instructions. This NVIDIA Compose example does not run on macOS Docker Desktop. VRAM must accommodate weights, the vision encoder and inference caches. Input size, context and concurrency affect total usage.

From the project root, copy `.env.example` only if `.env` does not exist. Replace both placeholder secrets with the **same** private random key:

```dotenv
WORKBENCH_MODE=community
QWEN_API_KEY=replace-with-one-private-random-key
WORKBENCH_MODEL_BASE_URL=http://qwen-vl:8000/v1
WORKBENCH_MODEL_NAME=qwen-vl
WORKBENCH_MODEL_API_KEY=replace-with-one-private-random-key
WORKBENCH_MODEL_REQUEST_PROFILE=compatible
WORKBENCH_MODEL_LABEL=Local Qwen-VL
```

```bash
chmod 600 .env
docker compose -f compose.yaml -f compose.qwen.yaml config --quiet
docker compose -f compose.yaml -f compose.qwen.yaml up --build -d
docker compose -f compose.yaml -f compose.qwen.yaml ps
docker compose -f compose.yaml -f compose.qwen.yaml logs --tail=100 -f qwen-vl
```

The first startup downloads weights and loads/compiles the model. The workbench waits for model health. Exit log following with `Ctrl+C`; containers continue running. Read the community login token:

```bash
docker compose -f compose.yaml -f compose.qwen.yaml exec workbench cat /app/data/access-token
```

The environment creates a community connection. For manual UI setup use `http://qwen-vl:8000/v1`, model ID **`qwen-vl`**, your `QWEN_API_KEY`, and compatible mode. The ID is the configured served-model alias, not necessarily its weight repository name.

Services share a Compose network and the model publishes no host port. Its weights persist in `qwen-model-cache`, outside the workbench image/Git. An API key alone is not a complete public gateway. Keep both `-f` arguments and the same project name for later operations. `down -v` deletes both business storage and the model cache.

Inspect the model list internally without sending documents:

```bash
docker compose -f compose.yaml -f compose.qwen.yaml exec -T qwen-vl python3 -c '
import json
import os
import urllib.request
request = urllib.request.Request(
    "http://127.0.0.1:8000/v1/models",
    headers={"Authorization": "Bearer " + os.environ["VLLM_API_KEY"]},
)
with urllib.request.urlopen(request, timeout=10) as response:
    print([item["id"] for item in json.load(response)["data"]])
'
```

Expect `qwen-vl` in the list. The key is read internally without printing. Existing remote services can skip GPU deployment and use their operator-supplied addresses.

For SaaS set `WORKBENCH_MODE=saas`, allow `http://qwen-vl:8000/v1`, sign in with the bootstrap account, and create each team's connection separately. See [SaaS operations](saas.md).

The example limits concurrency to one and context to 8192 tokens, allowing up to eight input images. **Allowing eight images does not mean eight pages fit.** Image tokens, prompts and output share the budget. Start with per-page OCR/tables and one-page custom extraction; reduce pages or tune hardware/context before trying joint extraction. Increasing only the workbench output limit cannot enlarge the server context.

## 4. Verify either connection

1. Select the new connection and full-text OCR in the workbench.
2. Upload/select `examples/synthetic-invoice.png` and run direct localization. This sends an actual model request: OpenAI incurs usage and self-hosting consumes GPU resources.
3. Inspect text, JSON, raw output and status.
4. Try `synthetic-table.png`, then `synthetic-table-multipage.pdf` for table structure and linked PDF page review.
5. Configure credential applications only after this first check. Evaluate authorized representative samples before publishing a fixed application version.

Localization, multiple instances and contextual refinement can add requests. Upload example files explicitly.

## 5. Troubleshooting

| Symptom | Check |
|---|---|
| 401 / 403 | Upstream project/model permissions and API key, not a workbench login token |
| 404 / missing model | Base path, retired models and the Qwen served alias |
| Discovery works, recognition fails | Actual image support; listing models is not an image test |
| OpenAI rejects parameters | Select modern OpenAI mode and save/select a new connection |
| 429 | Provider rate/quota limits; avoid repeated reruns that can add cost |
| Timeout | Connectivity from the backend container/server, not just your browser |
| System proxy has no effect | The adapter intentionally ignores ambient HTTP_PROXY/HTTPS_PROXY; use operator-managed reachable egress/gateways |
| Container localhost fails | Use `qwen-vl` in the merged Compose network; consult the Docker guide for host services |
| GPU startup fails/OOM | Driver compatibility, server logs, VRAM, context, input count and competing workloads |
| Truncated output/invalid JSON | Reduce input pages or raise output budget within server context limits |
| Slow first run | Separate weight download/compilation from warm inference; start with one page/direct localization |

Upstream error bodies are intentionally hidden to protect documents/secrets. Debug in controlled operator environments; do not publish private request bodies in Issues.

## 6. Protocol compatibility

Connection API/UI, environment initialization and CLI support `request_profile=compatible/openai`; CLI reads `WORKBENCH_MODEL_REQUEST_PROFILE`. No existing compatible connection is silently migrated. Azure-specific paths/authentication, Responses-only services, audio and image generation are outside this example.

References: [Qwen model card](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct), [vLLM Docker](https://docs.vllm.ai/en/stable/deployment/docker/), [server options](https://docs.vllm.ai/en/stable/cli/serve/), [v0.34.0 release](https://github.com/vllm-project/vllm/releases/tag/v0.34.0), [OpenAI vision](https://developers.openai.com/api/docs/guides/images-vision).
