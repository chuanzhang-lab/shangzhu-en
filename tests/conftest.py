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

隔离策略（两级，自动选择）：
1. **测试库优先**：PG 可达时自动建/连 `shangzhu_en_test` 测试库
   （PGDATABASE_URL 指向它）。库名与中文仓 `shangzhu_test` 物理隔离——
   两仓并行跑测试绝不互踩同一批表；
2. **PG 不可用**：PGDATABASE_URL 指向必败地址（端口 1 秒级 ECONNREFUSED），
   get_store() 降级到 LocalFileStore，且 LOCAL_STORE_PATH 强制指向临时目录——
   离线/无 PG 环境（含 CI）照样全量可跑、零外部依赖。

三闸兜底（每个用例前后自动执行）：
- 环境变量被个别用例 pop/篡改后（如 test_config_priority）自动恢复；
- 每个用例前 reset_store_for_tests()，杜绝单例缓存住错误后端；
- test_isolation_guard.py 断言测试期 store 永不指向真实库。
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

# ── 第二闸：测试库连接串 ────────────────────────────────────────────────────
# PG 可达则连测试库，不可达则给必败地址（快速失败 → get_store 自动降级 JSON）
_TEST_DB_NAME = "shangzhu_en_test"  # 勿与中文仓 shangzhu_test 共用：并行会互踩
_FALLBACK_DB_URL = "postgresql://nobody@127.0.0.1:1/shangzhu_en_test"  # 必败且快败

# 测试专用临时目录（LocalFileStore / 文件类测试的统一落点）
TEST_TMP_DIR = tempfile.mkdtemp(prefix="shangzhu-en-tests-")
os.environ["LOCAL_STORE_PATH"] = TEST_TMP_DIR


def _try_prepare_test_db() -> str:
    """尝试确保测试库存在，返回测试库连接串；PG 不可用返回必败地址。"""
    try:
        import psycopg

        base_url = f"postgresql://{os.environ.get('USER', 'postgres')}@localhost:5432/postgres"
        with psycopg.connect(base_url, connect_timeout=2) as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (_TEST_DB_NAME,))
                if cur.fetchone() is None:
                    cur.execute(f'CREATE DATABASE "{_TEST_DB_NAME}"')
        return f"postgresql://{os.environ.get('USER', 'postgres')}@localhost:5432/{_TEST_DB_NAME}"
    except Exception:  # noqa: BLE001 —— PG 不存在/无权限：走降级，测试照跑
        return _FALLBACK_DB_URL


PGDATABASE_URL_UNDER_TEST = _try_prepare_test_db()
os.environ["PGDATABASE_URL"] = PGDATABASE_URL_UNDER_TEST


@pytest.fixture(autouse=True)
def _isolate_store_env():
    """每个用例前后：恢复隔离环境变量 + 清 store 单例（防用例间互相污染）。"""
    os.environ["PGDATABASE_URL"] = PGDATABASE_URL_UNDER_TEST
    os.environ["LOCAL_STORE_PATH"] = TEST_TMP_DIR
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
