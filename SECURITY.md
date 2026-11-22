# 安全与数据边界

ChineseOCR Hub v0.5 是单进程 Alpha，提供两种显式部署模式。community 的工作空间令牌拥有全部权限，供受信任团队使用。saas 采用账户、实时团队成员校验、独立团队库与文件，以及 owner/admin/member/viewer 角色；平台管理员只能管理额度/账户元数据，不自动获得其他团队文档权限。

- 默认监听本机。在线入口需要 HTTPS、严格域名路由、网关请求体/速率/超时限制；禁止绕过网关直连。代理仅信任受控地址，不能把客户端伪造的转发头当作可信来源。
- 浏览器会话 HttpOnly、SameSite=Strict，HTTPS 时 Secure；在线会话服务器侧 12 小时到期、注销撤销。改密码撤销所有会话和接口密钥；停用账户/移除成员会阻断后续请求。在途调用无法撤回。
- scrypt 使用独立盐，N=32768/r=8/p=3，并限制并发散列。会话、API key、邀请随机令牌只保存 SHA-256 散列；API key 明文仅返回一次，固定团队和上限权限。邀请默认 7 天、单次使用；注册默认需邀请，无邮件验证。
- SaaS 模型密钥以 Fernet 加密，主密钥位于 `data/saas/provider-encryption.key`，权限 0600；主密钥与库同时泄漏仍可解密。缺失原密钥时拒绝启动。community 模型密钥在权限受限数据库中未加密。保护宿主机和完整备份。
- SaaS 对模型发现、保存与执行应用运维指定的精确 API Base URL 允许列表，默认禁止全部地址；模型请求禁止跟随重定向。运维必须控制允许域名、DNS 和网络出口，不能把租户任意地址加入允许列表。
- 应用发布固定模板/模型配置；管理写操作需要账户会话，发布/切换/停用需要 admin/owner。应用 Key 限制 /v1、指定应用、该 Key 的上传和任务；公开请求禁止提示词/模型覆盖，响应不返回配置或原始输出。旧团队 Key 保持原权限，需明确签发限定 Key。
- 文件只走鉴权路由，不暴露磁盘路径。CSP 禁止远程/内联脚本。API 校验错误不包含请求输入；模型错误不回显上游正文。审计只记录操作标识与有限元数据，不保存文档、提示词、密码或密钥。
- 数据只在显式提交任务时发往配置的模型。项目不含模型权重、使用统计、外部 CDN、固定业务答案。文档定位与实例识别也向同一配置模型提交图像；上传后不自动执行。
- 全文/定位输出属于不可信模型数据。提示词要求忽略文档指令，但不保证消除提示注入；输出只做结构校验与展示，不执行代码。Markdown 以 textContent 显示；来源框由服务器持久化结果读取，裁剪路由不接受客户端文件路径或任意矩形，仍校验当前团队任务权限。
- 文件大小、像素、页数、任务单元和响应体有界；原生图像/PDF 解析仍在应用进程内，尚非沙箱。面向不可信公网输入的正式生产服务需先部署独立解析隔离、入口限速与持续依赖维护，并完成安全与负载验证。
- 暂无自动保留期、删除 UI、在线备份和灾备编排。存储额度只计原件/页面，不替代宿主机磁盘配额。不要在公开 Issue 上传真实证件、医疗/财务文档、数据库或凭据。

密码和会话实现参考 [OWASP 密码存储](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)、[会话管理](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)；加密语义见 [Fernet 文档](https://cryptography.io/en/latest/fernet/)。参考这些资料不代表已通过第三方审计。

发现问题先避免公开敏感细节。请使用 [GitHub 私密漏洞报告](https://github.com/chineseocr/chineseocr-hub/security/advisories/new) 联系维护者 [wenlihaoyu](https://github.com/wenlihaoyu)。若私密报告入口暂不可用，可在公开 Issue 中仅提出不含漏洞细节的联系请求；不要上传文档、密钥、可利用步骤或客户信息。当前没有独立安全邮箱，不承诺未建立的响应 SLA。

Please use [private vulnerability reporting](https://github.com/chineseocr/chineseocr-hub/security/advisories/new). If unavailable, open a contact request without sensitive details; never publish credentials or customer files. No response SLA is promised.
