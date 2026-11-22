# Docker 部署与运维

[English](docker.en.md) · [页面操作手册](user-guide.md) · [在线版权限与额度](saas.md)

适用版本：**v0.6.0a1 Alpha**。镜像只运行工作台，不包含模型服务、模型权重或 GPU 运行时。当前提供 Dockerfile 和 Compose 源码构建方式，尚未发布可直接拉取的官方镜像。

## 1. 环境与目录

安装 Docker Engine（需支持 `host-gateway`，20.10+）和 Compose v2，或安装包含两者的 Docker Desktop。命令在解压的项目根目录执行，此目录中应有 `Dockerfile`、`compose.yaml` 和 `requirements.lock`。Windows 建议在 WSL2 Bash 执行本文命令。

```bash
docker version
docker compose version
```

默认容器限制为 2 CPU、1GiB 内存，这只是起始配置，不是容量承诺。多团队或复杂 PDF 应按实际测试调整 `compose.yaml`。工作台自身不需要 GPU；首次构建需要下载 Python 基础镜像和锁定依赖。

## 2. 社区版首次启动

仅在尚无 `.env` 时复制配置；已有部署直接编辑原文件，不覆盖其中的私有配置：

```bash
cp .env.example .env
chmod 600 .env
docker compose config --quiet
docker compose build --pull
docker compose up -d
docker compose ps
docker compose exec workbench cat /app/data/access-token
```

打开 [http://127.0.0.1:8765/?lang=zh](http://127.0.0.1:8765/?lang=zh)，填写最后一条命令读取的令牌。`WORKBENCH_TOKEN` 留空时自动生成令牌；若在 `.env` 中指定至少 16 字符的令牌，以该环境变量为准，不再读取旧令牌文件。只在受信任的本机终端读取，不把凭据复制到 Issue 或日志。

社区版共享一个有管理权的工作空间，适合个人或可信团队。数据在命名卷中；源目录的 `data/` 不会自动挂载或导入容器。已有本地数据需停止原服务后，通过备份恢复方式迁移。

## 3. 在线多租户版首次启动

在**首次启动之前**编辑 `.env`：

```dotenv
WORKBENCH_MODE=saas
WORKBENCH_ALLOW_REGISTRATION=0
WORKBENCH_ALLOWED_MODEL_BASES=https://vision.example.test/v1
```

将示例地址替换为平台允许使用的实际多模态服务。允许列表留空时禁止团队新建模型连接。然后执行与社区版相同的 build/up 命令，读取在线版初始账户：

```bash
docker compose exec workbench cat /app/data/saas/initial-admin.json
```

登录后进入「团队与用量」修改初始密码，再删除引导文件：

```bash
docker compose exec workbench python -c "from pathlib import Path; Path('/app/data/saas/initial-admin.json').unlink(missing_ok=True)"
```

默认采用邀请注册，不发送邮件。团队管理员在各自团队的「模型接入」保存连接。`WORKBENCH_MODEL_*` 环境连接只初始化社区版，**不会自动下发给在线团队**。模式切换不会迁移社区文档或将其分配到团队。角色、API Key、模型加密主密钥及额度见 [在线运维指南](saas.md)。

## 4. 接入真实多模态模型

完整案例见 **[Qwen-VL 自部署 / OpenAI API](model-connections.md)**。可选 `compose.qwen.yaml` 将 Qwen GPU 服务加入基础 Compose；需要 Linux/NVIDIA，后续操作均保留两个 `-f` 参数。

在页面「模型接入」填写 Base URL、API Key，检测并选择实际模型 ID。接口须接受 Chat Completions 和 PNG `image_url` data URL；发现模型列表成功后，仍需上传合成文档验证视觉能力。

社区版也可在 `.env` 中填写：

```dotenv
WORKBENCH_MODEL_BASE_URL=https://vision.example.test/v1
WORKBENCH_MODEL_NAME=your-vision-model
WORKBENCH_MODEL_API_KEY=your-private-key
WORKBENCH_MODEL_REQUEST_PROFILE=compatible
WORKBENCH_MODEL_LABEL=My vision service
```

修改 `.env` 后使用 `docker compose up -d --force-recreate` 应用新环境；`restart` 不会更新容器环境。Compose 的 `config` 完整输出可能包含密钥，日常校验使用 `config --quiet`。

| 模型位置 | Base URL 示例与条件 |
|---|---|
| 远端服务 | `https://vision.example.test/v1`，替换为真实地址 |
| Docker 宿主机 | `http://host.docker.internal:8000/v1`；Compose 已提供 host-gateway 映射 |
| 同一 Docker 网络的另一个容器 | `http://model-service:8000/v1`；需由运维将双方接入同一网络，当前 Compose 不启动模型容器 |

容器中的 `127.0.0.1` 指容器自身。Linux 宿主机模型必须监听容器网桥可达的接口，并允许相应防火墙流量；只监听宿主机 loopback 的服务不能靠 DNS 映射变得可达。不要为此把模型无鉴权暴露到公网。在线版允许列表应包含团队实际填写的、归一化后的同一 Base URL。

## 5. 配置、健康检查与日常命令

| 配置 | 默认与说明 |
|---|---|
| `WORKBENCH_IMAGE_TAG` | `0.6.0a1`，本地构建镜像标签；修改标签不会自动下载或切换源码 |
| `WORKBENCH_PYTHON_IMAGE` | `python:3.12-slim-bookworm`；发布方可换成已验证的完整摘要引用再重新构建 |
| `WORKBENCH_PORT` | `8765`，宿主机 loopback 端口；容器内固定 8765 |
| `WORKBENCH_MODE` | `community` 或 `saas` |
| `WORKBENCH_TOKEN` | 社区令牌；在线版使用账户登录 |
| `WORKBENCH_ALLOW_REGISTRATION` | `0`，在线版默认邀请注册 |
| `WORKBENCH_ALLOWED_MODEL_BASES` | 在线版精确允许列表，多个地址用逗号分隔 |
| `WORKBENCH_MODEL_REQUEST_PROFILE` | `compatible` 发送 max_tokens/temperature；OpenAI 新版模型选 `openai` |
| `WORKBENCH_MODEL_*` | 可选社区版初始化模型；在线团队不继承 |

容器数据目录固定为 `/app/data`。`.env.example` 中的 `WORKBENCH_DATA_DIR` 仅供本地 Python 使用，Compose 不用它改写卷挂载。默认命名卷在 Docker 中通常带 Compose 项目前缀；更改项目目录名或 `-p` 会选择另一个卷，不代表原数据被删除。已有部署应固定项目名。

```bash
curl --fail http://127.0.0.1:8765/healthz
docker compose logs --tail=100 -f workbench
docker compose exec workbench id
docker compose stop -t 200 workbench
docker compose start workbench
docker compose down
```

健康检查只证明 HTTP 存活，不检查模型可用性。容器用户 UID/GID 为 `10001:10001`，运行时不使用 root；日志每份最多 10MB、最多 3 份。普通 `down` 保留命名卷，**`down -v` 会删除卷及其中业务数据**。不要将同一数据卷用于多个工作台实例；不能通过增加 workers 或 `--scale` 扩容。

## 6. 停机备份

以下 Bash 命令先停止工作台，再用同一镜像的 Python 标准库将整个数据卷流式备份到宿主机；辅助容器不启动 Web 服务，也不调用模型。示例文件名固定，每次备份请更换名称，避免覆盖旧备份。

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

确认命令成功、备份可读，再恢复原服务：

```bash
tar -tzf backups/workbench-before-upgrade.tar.gz > /dev/null
docker compose start workbench
```

备份包含数据库、文档、令牌和在线版模型加密主密钥，应私密加密保存；不要提交源码或对外分享。`.env` 若有令牌/模型环境配置，需另外安全保存。只备份数据库无法完整恢复。`backups/` 已被 Git 忽略，Docker 构建也不会复制它。

## 7. 恢复到独立的新卷

仅恢复自己生成并信任的备份。先停止原部署以释放端口；下面用新项目名 `hub-restore` 创建独立卷，拒绝向非空目录解压，不覆盖原卷。若该项目名已使用，换一个新名称，并在全部命令中保持一致。新机器先构建与备份匹配的版本镜像。

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

确认原模式、环境配置、账户、文件、历史任务及模型连接可用。先用合成样本验证，恢复验证结束后再决定正式切换。保持新项目名才能继续访问恢复卷；不要同时启动占用相同端口的旧部署。解压失败会留下部分数据，应检查原因并使用另一个新空卷重试。

## 8. 升级与离线分发

先按第 6 节停机备份，再在**相同项目目录/项目名**更新到目标版本源码，保留私有 `.env`。如需回退，保留旧版本源码、镜像和升级前备份，避免旧程序直接打开已升级的数据。

```bash
docker compose build --pull
docker compose up -d
docker compose ps
```

如需离线安装，可在同架构联网机器构建后导出，再在目标机器导入：

```bash
docker save -o chineseocr-hub-0.6.0a1.image.tar chineseocr-hub:0.6.0a1
docker load -i chineseocr-hub-0.6.0a1.image.tar
docker compose up -d --no-build --pull never
```

`save/load` 是两台机器的先后操作；同时分发匹配版本的 Compose 与 `.env.example`，目标机自行填写私有 `.env`。镜像包不包含数据卷和 `.env`。默认只构建当前架构，不能把 arm64 镜像当作已验证的 amd64 镜像使用；尚未提供经验证的多架构官方镜像。

## 9. 排障与公网入口

| 现象 | 检查方式 |
|---|---|
| 端口已占用 | 修改 `.env` 的 `WORKBENCH_PORT`，重新 `up -d`，访问对应端口 |
| 页面可打开但模型不可用 | 检查服务实际模型 ID、图像支持、容器网络、API Key；健康检查不验证这些 |
| SaaS 模型地址被拒绝 | 检查允许列表与团队 Base URL 是否一致，改环境后重新创建容器 |
| 改 `.env` 不生效 | 使用 `up -d --force-recreate`，不是仅 `restart` |
| 看起来数据消失 | 检查项目名、目录名、模式及挂载卷；不要立即初始化或删除旧卷 |
| 出现工作空间锁错误 | 同卷已有进程；停止重复实例，不绕过文件锁 |
| 自行改绑宿主目录后 Permission denied | 目录须允许 UID/GID 10001 写入；默认命名卷无需手工改属主 |
| 镜像构建失败 | 检查基础镜像/依赖仓库可达性及目标架构；保留锁文件的哈希校验 |

默认只发布到宿主机 loopback。公网部署应由宿主机 HTTPS 反向代理转发到该端口，并设置域名、可信代理来源、请求体/速率限制和备份。不要直接把社区共享令牌当成面向不同客户的账号体系。配置示例与实现限制见 [部署说明](deployment.md)、[安全说明](../SECURITY.md)。

本文配置依据 Docker 官方 [Compose 服务配置](https://docs.docker.com/reference/compose-file/services/)、[命名卷](https://docs.docker.com/engine/storage/volumes/) 和 [一次性运行命令](https://docs.docker.com/reference/cli/docker/compose/run/)。2026-11-22 Linux CI 已通过实际镜像构建、非 root 社区容器及 SaaS 容器的鉴权/上传/隔离/重启持久化验证。Compose 两种模式的配置校验通过；整条 Compose 部署、真实数据备份恢复、其他架构和生产容量不在该次验证范围。本机仍未安装 Docker；详见 [验证记录](validation.md)。
