# 自部署运行 / Self-hosted deployment

v0.6.0a1 支持 community 本地单工作空间和 saas 在线多租户两种模式。默认 community；在线模式的角色、模型地址限制、用量和平台密钥操作见 [saas.md](saas.md)。当前均为单进程部署，无支付收款、SSO 或高可用。

容器的逐步构建、两种模式登录、模型网络、备份恢复、升级与排障见 [Docker 操作指南](docker.md)（[English](docker.en.md)）。页面实际操作见 [用户手册](user-guide.md)（[English](user-guide.en.md)）。本文补充 Python 持久化与公网入口约束。

## 启动与持久化

推荐 Python 3.12、macOS/Linux，或 Docker/WSL2。按 README 用锁文件安装，执行 `python -m workbench`。默认监听 `127.0.0.1:8765`；数据目录为当前目录的 `data/`。将整个数据目录保存在可写持久盘，不能只保存 SQLite 文件而丢弃原件/页面。

同一目录只运行一个进程，不加 `--workers`，不通过多容器共享这个目录扩容。系统文件锁会阻止第二个进程，避免启动恢复重置另一个实例的任务。

| 环境变量 | 意义 |
|---|---|
| `WORKBENCH_DATA_DIR` | 工作空间路径，CLI 也支持 `--data-dir` |
| `WORKBENCH_TOKEN` | 至少 16 字符的管理令牌；未设置时自动随机生成在 access-token 文件 |
| `WORKBENCH_MODEL_BASE_URL` | 可选模型根地址或 `/v1` 地址 |
| `WORKBENCH_MODEL_NAME` | 与模型地址一起设置，必须是服务实际提供的 ID |
| `WORKBENCH_MODEL_API_KEY` | 可选模型密钥，由服务要求决定 |
| `WORKBENCH_MODEL_REQUEST_PROFILE` | 默认 `compatible`；OpenAI 新版模型用 `openai`，见 [接入案例](model-connections.md) |
| `WORKBENCH_MODEL_LABEL` | 可选连接名称，未设置时使用模型 ID |

Python 启动不会自行读取 `.env`。在终端导出变量或由进程管理器注入。Docker Compose 会读取项目 `.env` 并传入列明的变量。没有配置模型时工作台仍可打开、编辑和上传，但不能执行 OCR、定位、提取或评测。所有视觉任务统一使用支持图像的多模态服务，不额外安装传统 OCR 引擎。全文与多文档的 API/CLI、调用次数和实例恢复见 [多模态指南](multimodal-ocr.md)。

自己的服务域名和密钥只存环境/私有数据库，不写进开源默认配置。服务根地址自动补 `/v1`；在页面点击模型发现确认 ID。发现操作不传文档，也不能证明模型一定具备视觉能力，应再用有授权的图片/PDF 验证。

## 对外部署

由运维在反向代理设置 HTTPS、访问控制、请求体与时间限制。示例 Nginx 关键参数：

```nginx
location / {
    proxy_pass http://127.0.0.1:8765;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    client_max_body_size 21m;
    proxy_read_timeout 30s;
}
```

提取是持久化后台任务，提交/轮询 API 不需要等待大模型整次返回。应用只应信任实际反向代理转发的头，不应绕过代理直接暴露服务端口。TLS 证书、代理 IP 信任、域名与网络策略由部署方配置；示例片段不是完整公网部署配置。

community 模式下，所有持有工作空间令牌的人都有管理权。密钥存在权限受限的 SQLite 中，未加密；模型地址允许访问本地网络以支持本地推理，所以配置接口只能向受信任管理员开放。HTTP 详细校验错误不回显输入值，模型错误不回显上游响应正文。

## 升级、恢复和备份

升级前停止服务并备份整个目录，再安装新版本。v0.2 会归档旧固定输出连接、对应任务/评测及精确识别出的初始合成样本，不删除其数据库内容，也不把它们改名冒充真实结果。用户模板改动和真实数据保留；界面不显示归档记录，直接读取会返回 410。

启动恢复 running 状态到队列，已有单元检查点跳过。取消为协作式；正在发送的调用不能撤回。中断时仍在调用且未落库的单元可能再次请求并计费。模型错误不会自动重试，管理员可明确选择重跑。

当前没有在线备份、保留期清理、细粒度删除或 HA。停止后整体备份目录；备份包含文档和密钥。持续维护依赖与基础镜像；源 Dockerfile 使用可变 Python 官方标签，正式发版的镜像摘要应由发布方固定并验证。

## English deployment notes

Use one process and a persistent data directory. Configure a real vision model in the UI or inject the paired BASE_URL/NAME environment variables; API keys are private runtime state. Python does not auto-load `.env`; Compose does. Default exposure is localhost.

Put authenticated HTTPS/network controls in front of a managed deployment. Community workspace tokens grant full access. Select saas mode for account-based tenant isolation and follow saas.md; do not expose community tokens to unrelated customers. Keep backups private. Recovery is at-least-once for in-flight model calls, cancellation is cooperative, and completed units are checkpointed. Linux CI has passed the image build and community/SaaS container smoke checks; complete operational acceptance remains separate. Follow the validation record before a public rollout.
