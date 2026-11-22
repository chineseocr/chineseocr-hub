# 测试与验证

适用版本：v0.6.0a1。自动化由 [GitHub Actions](https://github.com/chineseocr/chineseocr-hub/actions) 执行，模型接入与业务样本评测分别验证。

## 自动化覆盖

最近功能提交 `fa9e5e3` 的 [CI 记录](https://github.com/chineseocr/chineseocr-hub/actions/runs/45027828646) 已通过，测试集为 127 项。

| 检查 | 覆盖内容 |
|---|---|
| Linux / macOS | 上传、PDF 渲染、Schema、提示词、任务、评测和模型协议 |
| 几何与表格 | 归一化坐标、旋转/纠偏逆映射、行列带、线框辅助、合并跨度及 CSV/HTML |
| SaaS | 团队/应用/密钥隔离、角色撤销、会话、额度与事务回滚 |
| 模型参数 | 兼容模式、OpenAI 模式、旧连接默认值、图像 data URL 与密钥脱敏 |
| 代码与依赖 | Ruff 检查、格式检查、锁文件安装及 pip-audit |
| Docker | 镜像构建、非 root 社区容器、SaaS 鉴权/隔离及重启持久化 |
| Compose | 社区、SaaS 及 Qwen GPU 合并配置解析 |
| 源码分发 | 文件允许列表、校验和、源码一致性及解压后社区/SaaS 启动 |

## 本地运行

```bash
pip install --require-hashes -r requirements.lock
pip install -r requirements-dev.txt
python -m pytest -q
ruff check workbench tests scripts
ruff format --check workbench tests scripts
python scripts/generate_examples.py
python scripts/build_release.py
python scripts/verify_release.py dist/chineseocr-hub-0.6.0a1.tar.gz --smoke
```

## 界面检查

- 中文/English 切换与相应语言的教程链接。
- PDF 页签与来源页同步，字段/文字块/单元格与图像框双向选择。
- 图像缩放、位置框开关、空字段分组、结果视图和导出。
- 应用检索、证件配置安装、字段/提示词版本和模型参数模式选择。

## 模型与部署验收

项目合成票据、双页 PDF 及表格已用于实际多模态服务的字段、全文、定位和表格流程验证。它们用于检查端到端流程；业务评测应使用目标场景的数据集，固定模型、提示词、预处理与标准答案，再记录字段指标、耗时和用量。

Qwen GPU 配置已完成配置解析、镜像标签和启动入口核对；GPU 推理及 OpenAI 云端推理需在部署环境验证。完整 Compose 运维、备份恢复、其他架构、负载和安全验收由部署方执行。首次接入步骤见 [模型教程](model-connections.md)，样本操作见 [使用案例](../examples/use-cases.md)。
