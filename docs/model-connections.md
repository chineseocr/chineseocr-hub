# 模型接入：Qwen-VL 自部署与 OpenAI API

[English](model-connections.en.md) · [页面操作手册](user-guide.md) · [Docker 运维](docker.md)

适用：当前 v0.6 Alpha 源码，官方资料核对日期 **2026-11-22**。本教程区分“模型服务”和“工作台”：工作台负责上传、提示词、任务和结果；图像由你选择的模型识别。

| 你的情况 | 从哪里开始 |
|---|---|
| 没有 GPU，希望先跑一次识别 | 案例 A：OpenAI API，准备可用的 API 项目和密钥 |
| 希望自己部署 Qwen-VL | 案例 B：Linux/NVIDIA 上运行模型容器，再连接工作台 |
| 运维已提供模型地址 | 直接使用下面的页面字段表，先发现模型，再识别合成样本 |

## 1. 先分清三个地址和两种密钥

- **工作台地址**：例如 `http://127.0.0.1:8765`，供浏览器或业务系统访问。
- **模型 Base URL**：例如 `https://api.openai.com/v1`，工作台后端调用它。不要填 ChatGPT/Qwen 的聊天网页，也不要追加 `/chat/completions` 或 `/models`。
- **发布应用接口**：工作台自己的 `/v1/apps/{code}/recognitions`，供客户系统调用，详见 [应用 API](applications.md)。

模型 API Key 用来调用上游模型，填写在「模型接入」。工作台登录令牌/应用 API Key 用来访问本项目，不能互换。模型密钥不应填写到提示词或 URL。

工作台使用 **Chat Completions**：`GET /v1/models` 发现模型，`POST /v1/chat/completions` 发送 system/user 消息及 PNG data URL，读取文本结果。它不是仅支持 Responses API 的客户端，也不调用图像生成模型。

## 2. 案例 A：普通用户接入 OpenAI API

### 准备 API 项目

1. 登录 [OpenAI API 平台](https://platform.openai.com/)，在有权限的项目中创建 API Key，并确认模型权限及可用额度。不要将密钥发到 Issue 或提交到 Git。
2. 按 [Docker 指南](docker.md) 或 README 启动工作台并登录；在线版还需要选择正确团队。
3. 在线版由运维在 `.env` 配置 `WORKBENCH_ALLOWED_MODEL_BASES=https://api.openai.com/v1` 并重新创建工作台容器。团队 admin/owner 再保存模型连接；社区版不需要该允许列表。

### 页面应填写什么

进入「模型接入」，使用下面的具体案例：

| 页面字段 | 示例值 |
|---|---|
| 连接名称 | `OpenAI 视觉提取`，只是你自己的名称 |
| 服务地址 | `https://api.openai.com/v1` |
| 模型标识 | `gpt-6-luna`，需由你的项目实际授权 |
| 接口密钥 | 你刚创建的 OpenAI 项目 API Key |
| 接口参数模式 | **OpenAI 官方 API · 新版参数** |
| 最大输出词元数 | 初次可填 `8192`，工作台允许 128–16384 |
| 超时 / 秒 | 初次可填 `180`，工作台允许 5–180 |

先填写模型标识，再点击「检测连接并获取模型」。列表可能包含文本、音频、嵌入等其他模型；需要确认实际 ID，不把列表第一项当成视觉模型。检测只验证模型列表接口，不发送文档，也不验证识别精度。点击「保存并选择此连接」。

该案例选择官方支持图像输入与 Chat Completions 的 [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)，不保证每个账户均可使用。模型停用与替代信息以 [官方弃用表](https://developers.openai.com/api/docs/deprecations) 为准；不再把已标记弃用的 GPT-4.1 系列作为新部署默认教程。

### 为什么要选择 OpenAI 参数模式

`openai` 模式发送 `max_completion_tokens`，省略 `temperature`；`compatible` 模式保留 `max_tokens` 和 `temperature=0`，用于 Qwen/vLLM 等兼容服务。两者都使用 Chat Completions，不会失败后自动换参数重试。旧连接缺少该字段时继续使用兼容模式；要切换请保存一条新连接，再在应用发布中选择它。

新模式的输出预算可能包含推理词元，不等于可以输出同样数量的业务文字。全文/表格输出被截断时，可提高预算到工作台上限、缩小页范围或改逐页处理。参数定义见 [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)。

### 社区版的环境配置方式

不想在页面初次填写时，可编辑私有 `.env`：

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

模型名与 Base URL 必须一起配置。环境初始化默认输出预算 4096、超时 90 秒；需要上表的预算/超时时，使用页面新建连接。已有任务仍使用原连接。SaaS 团队不会继承平台的 `WORKBENCH_MODEL_*` 配置，应由团队管理员在页面独立填写。

## 3. 案例 B：自部署 Qwen-VL

### 服务器准备

本案例使用 **`Qwen/Qwen3-VL-8B-Instruct` + `vllm/vllm-openai:v0.34.0`**。这是一组固定教程版本，不承诺是未来最新版本。模型名称含 `VL`，不要误用仅文本的 Qwen，也不要直接换成 Thinking/量化版本而假设配置和显存需求不变。

由服务器管理员准备 Linux、兼容的 NVIDIA 驱动、Docker/Compose v2 和 [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)。先运行 `nvidia-smi` 确认宿主机驱动，再按 NVIDIA 官方说明验证容器可使用 GPU。此 GPU Compose 例子不适用于 macOS Docker Desktop。

显存需求取决于权重、视觉编码器、图像数量、上下文及并发；8B 权重可以装入不代表多页推理也能装入。先单页、单并发验证，再据实测增加资源或选择更小的 VL 模型。本机没有 NVIDIA GPU，不提供未经测试的显存承诺。

### 启动工作台和模型两个服务

在项目根目录，首次部署且没有 `.env` 时复制 `.env.example`。然后编辑私有 `.env`，两个模型密钥值必须相同；示例文字应替换为你自己的随机密钥：

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

首次要下载权重并加载/编译，可能较久。模型健康检查通过后工作台才启动。`Ctrl+C` 退出日志跟随不会停止后台容器。读取工作台令牌：

```bash
docker compose -f compose.yaml -f compose.qwen.yaml exec workbench cat /app/data/access-token
```

打开工作台，社区环境配置会创建默认连接。也可在「模型接入」手动填写：服务地址 `http://qwen-vl:8000/v1`、模型标识 **`qwen-vl`**、密钥为 `QWEN_API_KEY` 的实际值、参数模式选兼容服务。这里的模型 ID 是 Compose 的 `--served-model-name`，不是必须填写 Hugging Face 仓库名。

这两个容器在同一 Compose 网络中通信；模型没有发布宿主机端口。模型缓存位于独立的 `qwen-model-cache` 命名卷，不会加入工作台镜像或 Git。vLLM 自带的 API Key 不是完整公网网关，例子通过不公开模型端口限制入口。

使用此部署时，后续 `exec/logs/stop/up/down` 也使用相同的两个 `-f` 参数和项目名。不要随意改项目名导致连接到新数据卷。`down -v` 会同时删除工作台数据卷及模型缓存。

### 只检查模型服务，不发送文档

在模型容器内部检查授权模型列表：

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

预期列表包含 `qwen-vl`。密钥从容器环境读取，不打印。已有远端模型服务可跳过本例 GPU 部署，使用管理员提供的地址和实际 ID。

在线版改为 `WORKBENCH_MODE=saas`，允许列表设置为 `http://qwen-vl:8000/v1`；读取在线版初始账户登录，再由每个团队保存连接，详见 [SaaS 运维](saas.md)。

### 上下文、图像数与 PDF

教程配置为单并发、8192 上下文词元、最多 8 张图像。**允许 8 张不代表 8 页一定放得下**：页面图像词元、提示词和输出共同占用上下文，也会增加显存。先全文 OCR/通用表格逐页测试，联合字段提取先选一页；上下文不足时缩小页范围或根据硬件调整 `compose.qwen.yaml`，重新创建模型容器。不能只提高工作台输出上限，忽略服务的总上下文。

## 4. 两个案例接入后都这样验收

1. 工作台选择新连接和「全文 OCR · 多模态识别」。
2. 上传源码中的 `examples/synthetic-invoice.png`，仅选此文件，使用直接定位开始识别。此时才发送页面 PNG 和提示词，OpenAI 会产生 API 用量，自部署也会占用 GPU。
3. 检查原文、原始响应、JSON 和任务状态。模型列表成功但任务失败时，按下表检查。
4. 再用 `examples/synthetic-table.png` 测试通用表格，用 `synthetic-table-multipage.pdf` 测试页签和来源联动。
5. 最后才安装并调试对应证件应用，使用有授权的代表性样本验收，保存提示词版本、评测后再发布。

普通识别、表格、字段提取都使用所选连接；多证件和精细定位可能追加多次模型调用。接通服务不代表文字、坐标或证件字段已达到业务精度。测试文件须手动上传；模型镜像和工作台运行镜像不预装客户样本。

## 5. 常见错误

| 现象 | 怎么排查 |
|---|---|
| 401 / 403 | 模型 API Key、项目权限是否正确；不要使用工作台登录令牌代替 |
| 404 / 模型不存在 | Base URL 是否误带 endpoint；模型是否下线；Qwen 别名是否为 `qwen-vl` |
| 检测成功，识别却失败 | 模型是否支持图像；发现只检查 `/models`，不是视觉验收 |
| OpenAI 400 / 参数不支持 | 确认选用 OpenAI 新版参数模式；旧连接需新建后重新选择 |
| 429 | 检查上游速率/额度，减少并发；不要连续重跑以免增加费用 |
| 连接超时 | 从工作台所在容器/服务器检查网络；浏览器能访问不代表后端可达 |
| 设置系统代理后仍不通 | 后端刻意忽略 `HTTP_PROXY/HTTPS_PROXY` 环境变量；本教程不提供自动代理配置，应由运维提供可达的出口或受控网关 |
| 容器里填 localhost 不通 | 它指当前容器。Qwen 合并 Compose 用 `qwen-vl`；其他宿主机服务参考 Docker 指南 |
| Qwen 模型容器退出/OOM | 检查驱动、vLLM 日志、显存、上下文和图像数，避免同时跑占满显存的其他服务 |
| 输出截断或 JSON 不完整 | 减少页数或增加允许的输出预算，并同时确认模型上下文足够 |
| 识别很慢 | 首次权重加载/编译与首次推理不同；先单页直接定位，再考虑多页和精细定位 |

页面错误不会回显上游完整响应，以免泄露密钥或文档；定位问题需要在运维控制的环境中排查，不要把私有请求全文贴到公开 Issue。

## 6. 兼容性与验证范围

当前连接配置、API、环境初始化和 CLI 都支持 `request_profile=compatible/openai`，CLI 使用 `WORKBENCH_MODEL_REQUEST_PROFILE`；已有兼容连接继续可用。模型能力和账号权限以服务方为准。Azure OpenAI 的专用路径/鉴权、仅 Responses API、音频接口和图像生成接口不在本例适配范围。

已用本地 HTTP 协议夹具检查旧连接、兼容模式和 OpenAI 模式的实际请求、图像 data URL、密钥脱敏、单次调用，以及环境初始化兼容性；这不是 OpenAI 真实账号或 Qwen GPU 的推理实测。**本机没有部署 Qwen GPU，也未为此教程调用真实 OpenAI API**。已有其他真实多模态服务验证见 [验证记录](validation.md)。

官方依据：[Qwen3-VL 模型卡](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct)、[vLLM Docker](https://docs.vllm.ai/en/stable/deployment/docker/)、[vLLM 参数](https://docs.vllm.ai/en/stable/cli/serve/)、[v0.34.0 发布](https://github.com/vllm-project/vllm/releases/tag/v0.34.0)、[OpenAI 视觉输入](https://developers.openai.com/api/docs/guides/images-vision)。
