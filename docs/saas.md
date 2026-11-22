# 在线多租户部署 / Online multi-tenant operations

仓库和包名 `chineseocr-hub`，产品名 ChineseOCR Hub。Python 模块保持 `workbench`，已有 `WORKBENCH_*` 变量继续可用。在线模式必须显式启动；不会把本地版数据自动迁入某个租户。

容器首次部署、账户初始化及完整备份/恢复命令见 [Docker 指南](docker.md)（[English](docker.en.md)）；业务页面使用见 [操作手册](user-guide.md)。

## 启动与模型策略

```bash
export WORKBENCH_ALLOWED_MODEL_BASES=https://vision.example.test/v1
python -m workbench --mode saas --data-dir data
```

`--mode` 默认使用 `WORKBENCH_MODE`，未设置为 community。`WORKBENCH_ALLOW_REGISTRATION=1` 才允许无邀请注册；默认关闭。`WORKBENCH_ALLOWED_MODEL_BASES` 是逗号分隔的精确模型 API Base URL；根地址会统一为 `/v1`。空值禁止全部目标。修改后重启；队列执行时再次检查允许列表。只列出运维控制的服务，不接受租户随意指定网络地址。

首次启动自动生成 `data/saas/initial-admin.json`，权限 0600。私下读取并登录，修改密码后删除此初始文件；不将其复制进源码或日志。初始密码不会出现在服务日志。修改密码会撤销该账户所有团队会话和接口密钥。忘记密码目前没有邮件找回，运营方需执行受控离线恢复流程；不能通过删除整个数据库来重置线上用户。

Compose 在 `.env` 设置 `WORKBENCH_MODE=saas` 和允许列表后，`docker compose up --build -d`。从容器 `/app/data/saas/initial-admin.json` 读取初始凭据。命名卷持久化，停止宽限期 200 秒。Python 运行不自动读取 `.env`。模型的 `WORKBENCH_MODEL_*` 环境连接只用于 community，不会泄漏给在线团队。

## 应用发布与客户调用

应用目录写操作要求 member 账户会话，发布/历史切换/停用要求 admin/owner 会话。可创建带 application_ids 的应用 Key；它只能调用 /v1，且文件与任务归属该 Key。旧团队 Key 不自动缩权。公开 API 进入同一团队网关，遵循实时成员关系、角色、额度、允许模型目标与全平台并发限制。操作见 [应用指南](applications.md)。

## 权限矩阵

| 操作 | viewer | member | admin | owner | 平台管理员 |
|---|---|---|---|---|---|
| 读取本团队配置/文件/结果 | 是 | 是 | 是 | 是 | 仍需成员角色 |
| 提交提取/评测，编辑字段/提示词/字典 | 否 | 是 | 是 | 是 | 仍需成员角色 |
| 模型连接保存/发现 | 否 | 否 | 是，会话 | 是，会话 | 仍需团队 admin/owner |
| 管理普通成员与邀请 | 否 | 否 | 是 | 是 | 仍需团队 admin/owner |
| 管理团队管理员 | 否 | 否 | 否 | 是 | 仍需 owner |
| 查看团队审计 | 否 | 否 | 是 | 是 | 仍需团队 admin/owner |
| 配置全平台团队额度/停用账户 | 否 | 否 | 否 | 否 | 是 |

每个成员可以创建自己的 member/viewer API key，但不能超过本人角色；只读成员只能创建只读密钥。密钥 1–90 天有效，每成员每团队最多 20 个有效密钥。控制面管理只接受账户会话，API key 不能切团队、邀成员、改模型或管理平台。成员降级立即限制后续密钥请求；成员移除直接撤销该团队密钥。团队所有者角色不能移除/降级；尚无所有权转交 UI。

邀请通过私下分享链接或令牌完成，应用不发送邮件。链接令牌放在 URL fragment，避免 HTTP 查询日志记录；接受后清理页面片段。待接受邀请占成员额度，撤销/过期释放保留名额。登录用户若失去最后一个团队，可新建团队或接受邀请恢复访问。

## 用量与套餐

初始每团队每月 1000 提交页、256MiB 原件/预览存储、10 成员。平台可调整 starter/pro/custom 标签与额度；这些标签**没有自动购买、计费、退款或扣款语义**。

页数按 UTC 自然月入队时预占：联合模式按实际输入页，逐页按各单元页数相加，评测按全部样本输入页相加。失败、取消和手工重跑计入提交量；相同幂等请求复用原任务不重复扣量。整批评测额度不足时任务/运行/预占全部回滚。多文档每页一次定位再逐实例识别，最多 12 实例/页；所有调用受同一并发和目标允许列表限制，页额度不按调用次数收费。已报告定位和实例词元随检查点保存；模型没有报告 usage 时不估算词元；模型费用以服务方账单为准。

存储额度包括上传原件和生成页面 PNG；不包括 SQLite、日志、备份和临时处理中间文件，不能替代磁盘隔离。已有超额数据不会被自动删除；降低额度后拒绝超额新增。当前无用户删除 UI，运维需监控磁盘与配置增长。

## 持久化、HTTPS 与容量

```
data/saas/
  platform.sqlite3          # 账户、成员、会话散列、邀请、审计
  provider-encryption.key   # 模型密钥加密主密钥，绝不能丢失
  initial-admin.json        # 首次引导凭据，改密后删除
  tenants/<server-id>/      # 各团队独立数据库及私有原件/页面
```

同目录只运行一个进程；系统锁阻止多进程同时接管。最多 100 个团队、每账户最多创建 10 个团队；每团队一个顺序 worker，全平台最多 4 个模型调用。这是实现限制，不是经压力测试确认的承载或 SLA。单次模型请求最多 180 秒，停止应给予至少 200 秒。中断中的模型调用可能恢复后重复计费。

公网域名必须通过 HTTPS 反向代理，仅允许配置域名，拒绝默认虚拟主机；仅信任代理实际来源 IP。默认 uvicorn 只信任本机代理；跨容器代理需正确设置可信转发来源，不能无差别信任客户端。配置网关每 IP 请求速率、连接/请求体限制和解析隔离后，再对公众开放。账号登录已有短窗节流，但不能替代 WAF/网关全局限流。

停止服务后备份整个 data/saas（含加密主密钥和所有租户文件），私密加密存储并验证恢复。只备份 SQLite 无法恢复文档，只备份库不备份主密钥无法恢复模型凭据。升级前先备份，禁止多容器共享目录扩容。

当前没有 PostgreSQL、对象存储、独立任务服务、HA、SSO、邮件验证、自动支付、保留期清理、解析沙箱和人工审核。已接入多模态文档定位与逐实例识别，但模型定位尚未经业务数据集验收。Linux CI 已完成基础容器构建、权限与重启持久化验证，见 [验证记录](validation.md)。可以开展受控试点；面向公众生产前仍需完成实际部署网络策略、恢复演练、安全审查和容量验收。

## English quick reference

Select `--mode saas`; read the generated private bootstrap credentials, change the password, then remove the bootstrap file. Registration defaults to invitation-only. Set exact approved API bases through `WORKBENCH_ALLOWED_MODEL_BASES`; a blank list denies all model destinations. Platform administrators manage metadata and limits without implicit document access.

Back up the complete stopped SaaS directory, including `provider-encryption.key`. API keys are tenant-bound and permission-capped. Admission quotas count submitted pages per UTC month, including failures/cancellation/retries; storage quotas count originals/previews. Provider token usage is reported only when supplied. No payment collection or subscription billing is implemented.

This is a single-process Alpha with separate local tenant stores, at most 100 teams and four concurrent model calls. HTTPS, trusted-host routing, ingress rate limits, parser isolation and production load/security validation remain deployment responsibilities; HA/SSO/email verification are not implemented.
