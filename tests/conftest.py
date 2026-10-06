"""pytest 全局闸：locale 钉 + 存储隔离（E-01，移植自 shangzhu M-01 并本地化）。

═══ 第一闸：locale 钉（E-03 反转：zh → en）══════════════════════════════

背景：产品部署语言是 en（`i18n.DEFAULT_LOCALE`），测试默认语言必须与之一致
——E-03 之前钉 zh 是历史包袱（套件从中文仓移植，大量用例喂中文输入、断言
中文输出），测的是 en 部署下**不可达**的链路，633 全绿是假象。

机制（优先级从高到低，见 `i18n.get_locale`）：
    会话级 ContextVar（set_locale） > 部署级 env（SHANGZHU_LOCALE） > 默认（en）

- 本文件在**部署级**把 SHANGZHU_LOCALE 钉成 en → 主路径全测 en 链路；
- 需要 zh 的用例（如中文规则回归）用 `set_locale("zh")` 在**会话级**覆盖，
  优先级高于本文件，不受影响；
- `test_i18n_guard` 里测「未设 env 时默认 en」的用例用
  `monkeypatch.delenv("SHANGZHU_LOCALE")` 临时摘掉本钉子，直接探到默认值。

为什么用 env 而非 autouse fixture 设 locale：两个 autouse fixture 的执行顺序不直观、
会随 pytest 版本漂移；env 是 `get_locale()` 回退链里的确定一环，会话级覆盖
天然压过它，无需约定顺序。

E-03 处置（2026-10-04，见 docs/test-reclassification-en-20261004.md）：
zh-only 140 条退役、en-reachable 66 条换英文输入/断言、language-neutral 439 条保留。

═══ 第二闸：存储隔离（E-01 新增）══════════════════════════════════════

背景：历史上测试直写真实 PG —— `test_local_store::test_postgres_roundtrip`
直接 `PostgresStore()` 打主库累积 367 条垃圾；`test_cors_config::_make_client()`
的 mock 只在 TestClient 构造期生效，请求期走真 store 落 12 条 xhr-task。
2026-10-04 实测主库 tasks 648 条（存活 8），仍在持续增长。

隔离策略（2026-10-06 起大幅简化）：持久化换成 SQLite 单文件后，**测试库就是
一个临时目录下的 .db 文件**——不再需要「建库 / 连管理库 / 判 PG 是否可达」
那一整套，也不存在跨仓互踩（中文仓用的是自己的 shangzhu_test 库）。
本文件只需把 `SHANGZHU_DB_PATH` 钉到会话级临时目录即可。

三闸兜底（每个用例前后自动执行）：
- 环境变量被个别用例 pop/篡改后（如 test_config_priority）自动恢复；
- 每个用例前 reset_store_for_tests()，杜绝单例缓存住错误后端；
- test_isolation_guard.py 断言测试期 store 永不指向真实数据文件。
"""
import os
import sys
import tempfile

import pytest

# ── 第一闸：locale 钉（force，非 setdefault）：测试默认语言=部署语言（en）。──
# 仅作用于测试进程，不影响生产运行（生产 en 由 start.sh 导出）。
os.environ["SHANGZHU_LOCALE"] = "en"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

# ── 第二闸：测试库落点（临时目录里的 SQLite 文件）──────────────────────────
TEST_TMP_DIR = tempfile.mkdtemp(prefix="shangzhu-en-tests-")
TEST_DB_PATH = os.path.join(TEST_TMP_DIR, "shangzhu_en_test.db")
os.environ["SHANGZHU_DB_PATH"] = TEST_DB_PATH
# 自动备份测试闸（S5）：测试期写操作数百次，不开闸每 200 次写就会触发
# 真实备份污染 backups/。需要验证自动备份链路的用例自己 monkeypatch 打开。
os.environ["SHANGZHU_AUTO_BACKUP"] = "0"


@pytest.fixture(autouse=True)
def _isolate_store_env():
    """每个用例前后：恢复隔离环境变量 + 清 store 单例（防用例间互相污染）。"""
    os.environ["SHANGZHU_DB_PATH"] = TEST_DB_PATH
    try:
        from storage.local_store import reset_store_for_tests

        reset_store_for_tests()
    except Exception:  # noqa: BLE001
        pass
    yield
    try:
        from storage.local_store import reset_store_for_tests

        reset_store_for_tests()
    except Exception:  # noqa: BLE001
        pass
