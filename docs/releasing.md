# 发布检查 / Release checklist

当前版本 0.6.0a1，源码根目录为独立项目，产品 ChineseOCR Hub，仓库为 [chineseocr/chineseocr-hub](https://github.com/chineseocr/chineseocr-hub)。源码提交、版本标签、GitHub Release、PyPI 和镜像分发是不同的发布步骤；提交源码不代表已完成其他分发。

```bash
pip install --require-hashes -r requirements.lock
pip install -r requirements-dev.txt
python -m pytest -q
ruff check workbench tests scripts
ruff format --check workbench tests scripts
pip-audit -r requirements.lock --disable-pip --no-deps --progress-spinner off
python scripts/dependency_inventory.py
python scripts/build_release.py
python scripts/verify_release.py dist/chineseocr-hub-0.6.0a1.tar.gz --private-workspace data --smoke
```

打包只走显式允许列表；排除 data、.env、虚拟环境、缓存、权重和历史构建。校验校验和、全部路径、逐字节源码一致性，并可私下扫描已知访问令牌、模型密钥、真实服务主机、在线主密钥及引导密码。已知值扫描不能替代人工隐私审查；截图、示例和提交历史仍需检查。不得直接打包父目录或执行 `git add .` 把旧客户工程带入。

若更新依赖，在干净 Python 3.12 环境用 `pip-compile --generate-hashes --no-emit-index-url --no-emit-trusted-host requirements.txt -o requirements.lock` 重建。同步第三方声明和 inventory，保留 PDFium、Pillow、cryptography/OpenSSL 等分发许可。运行 `pip check`。发布 Python wheel 时另行安装 build，执行 `python -m build --wheel` 并在独立环境验证入口、静态资源和 SaaS 模块。

CI 已配置 Linux/macOS、运行依赖审计、社区及在线容器鉴权/上传/权限/重启持久化检查。必须看到真实 CI 通过再记录为通过；本机无 Docker 时不能以编写 Dockerfile 代替验证。镜像发布前固定并验证基础镜像摘要及目标架构，准备私有备份恢复演练。

逐步容器操作与演练命令见 [Docker 指南](docker.md)（[English](docker.en.md)）。README 的新用户入口应指向 [页面操作手册](user-guide.md)（[English](user-guide.en.md)），并保留模型能力、费用与精度边界。

公开前维护者应配置仓库描述、Topics、维护者、GitHub 私密漏洞报告和有效联络方式，核对 Apache-2.0 适用范围及第三方声明。线上试点前按 saas.md 验证域名/HTTPS/可信代理/出口策略/解析隔离，完成授权数据集效果和容量验收。没有代表性评测不得宣称商用精度；没有外部安全审计不得宣称已审计。
