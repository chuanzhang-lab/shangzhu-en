# 创业者工作台 (A) — 常用工程命令
.PHONY: help sync test smoke start health compile lint hooks e2e drift

PY ?= .venv/bin/python3
# 端口统一口径：与 start.sh / web_server.py 默认值一致（8081）
PORT ?= 8081

help:
	@echo "make sync     - uv sync 安装主依赖 (A)"
	@echo "make test     - 全量回归（pytest 收集 tests/ 全部 33 个测试文件）"
	@echo "make smoke    - 导入 + health 结构冒烟（不启服务）"
	@echo "make compile  - 字节码编译检查"
	@echo "make lint     - ESLint 前端门禁（no-shadow/no-undef 错误级）"
	@echo "make e2e      - 浏览器端到端冒烟（真 Chromium：新建任务 + 零报错断言）"
	@echo "make drift    - 双副本 drift 报告（EN 违规硬失败；中文仓违规仅 DRIFT 警告）"
	@echo "make hooks    - 安装本地 pre-commit 门禁（版本化 .githooks/）"
	@echo "make start    - 启动本地服务 (PORT=$(PORT))"
	@echo "make health   - curl /health（需服务已启动）"

sync:
	uv sync

# 门禁单一真相源：pytest 全量收集。
# 说明：tests/run_all.py 只能扫模块级用例（类方法风格的文件收不到），
# 保留它作为无 pytest 环境的轻量后备，但以本目标为交付门禁。
test:
	$(PY) -m pytest tests/ -q --no-header -p no:cacheprovider

smoke:
	$(PY) -c "import web_server as w; h=w.app.router.routes; assert any(getattr(r,'path',None)=='/health' for r in h); print('smoke_ok', w.APP_VERSION, w.MODEL_NAME)"

compile:
	$(PY) -m compileall -q src web_server.py tests

# E-04 面状门禁：目录级扫描 src/web_static/**/*.js，新增前端文件自动纳入。
# no-shadow/no-undef 错误级（事故形态 → 面规则），其余 warn 不阻塞。
lint:
	npx eslint src/web_static

# E-10 浏览器 e2e：脚本自带隔离（临时 JSON 存储 + 必败 PG 地址），
# 不依赖本地 PG，不碰任何真实数据。chromium 走 ~/Library/Caches/ms-playwright 缓存。
e2e:
	node tests/e2e/smoke.mjs

# E-11 双副本 drift 护栏：三区边界（同源锚点/有意分化区/EN 独有不变量）。
# 只读取中文仓做对比，零写入；中文仓不可达时自动跳过跨副本对比（CI 常态）。
drift:
	$(PY) scripts/copy_drift_report.py

# E-09 本地提交门禁：与 .github/workflows/ci.yml 同口径三件套。
# 钩子本体版本化在 .githooks/，这里只做一次性激活（重克隆后跑一次即可）。
hooks:
	chmod +x .githooks/pre-commit
	git config core.hooksPath .githooks
	@echo "pre-commit 已激活（core.hooksPath → .githooks）"

# start.sh 读取 PORT 环境变量（不接受 -p 参数，见 start.sh）。
# SHANGZHU_LOCALE 显式传 en：部署默认语言必须是英文，不靠 start.sh 内部默认
# 传递（少一层隐式依赖）。注意**不全局 export** —— make test 走 tests/conftest.py
# 钉的 zh（中文用例需 zh），全局 en 会把 208 个中文用例跑崩。
start:
	PORT=$(PORT) SHANGZHU_LOCALE=en ./start.sh

health:
	curl -sS "http://127.0.0.1:$(PORT)/health" | $(PY) -m json.tool
