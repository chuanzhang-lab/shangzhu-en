"""E-08 护栏：except Exception 审计表是代码现场的库存契约。

中文仓 M-10 事故教训：吞异常的现场会无声增殖。本护栏锁两件事：
① 审计表逐条有档位、档位合计与统计一致（A31/B26/C10 = 67）；
② 逐文件现存 `except Exception` 数 == 表内行数 − 已收窄行数——
   新增或删除现场必须同步改表，否则这里红。
"""

import ast
import re
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
AUDIT = REPO / "docs" / "except-audit-en-20261004.md"
ROW_RE = re.compile(r"^\|\s*([A-Za-z0-9_./]+\.py):(\d+)\s*\|\s*([ABC])\s*\|")
# 2026-10-06：持久化由 PG 换成 SQLite，删掉 PostgresStore / LocalFileStore
# 后审计表从 67 行降到 62 行（A31→28 / B26→25 / C10→9）。这 5 处不是「被
# 收窄」，是**连失败面本身都没了**——换 SQLite 的实际收益之一。
# 同日 C1 加固新增 _execute 事务归口的 rollback 现场（A28→29，共 63 行）。
EXPECTED_TIERS = {"A": 29, "B": 25, "C": 9}


def _audit_rows():
    rows = []
    for line in AUDIT.read_text(encoding="utf-8").splitlines():
        m = ROW_RE.match(line)
        if not m:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append({
            "file": m.group(1),
            "line": int(m.group(2)),
            "tier": m.group(3),
            "desc": cells[2],
            "action": cells[3],
            "narrowed": "收窄" in cells[3],
        })
    return rows


def _count_exception_handlers(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sum(
        1
        for n in ast.walk(tree)
        if isinstance(n, ast.ExceptHandler)
        and isinstance(n.type, ast.Name)
        and n.type.id == "Exception"
    )


def _scoped_files():
    files = [REPO / "web_server.py"]
    files += sorted(REPO.glob("src/**/*.py"))
    files += sorted(REPO.glob("config/**/*.py"))
    return [f for f in files if f.is_file()]


def test_audit_table_wellformed():
    rows = _audit_rows()
    assert len(rows) == 63, f"审计表应有 63 行，实得 {len(rows)}"
    tiers = Counter(r["tier"] for r in rows)
    assert dict(tiers) == EXPECTED_TIERS, f"档位合计漂移: {dict(tiers)}"
    for r in rows:
        assert r["desc"] and r["action"], f"行缺现象/处置: {r['file']}:{r['line']}"


def test_live_sites_match_audit_inventory():
    """库存契约：每文件现存 except Exception 数 == 表行数 − 已收窄行数。"""
    expected = {}
    for r in _audit_rows():
        slot = expected.setdefault(r["file"], 0)
        expected[r["file"]] = slot + (0 if r["narrowed"] else 1)
    mismatch = {
        f: (want, _count_exception_handlers(REPO / f))
        for f, want in expected.items()
        if _count_exception_handlers(REPO / f) != want
    }
    assert not mismatch, f"现场与审计表不一致（期望, 现存）: {mismatch}"


def test_no_unaudited_silent_handlers_in_scope():
    """审计范围（web_server + src/ + config/）内不允许存在未入表的 except Exception。"""
    audited = {r["file"] for r in _audit_rows()}
    stray = {
        str(f.relative_to(REPO)): _count_exception_handlers(f)
        for f in _scoped_files()
        if _count_exception_handlers(f) and str(f.relative_to(REPO)) not in audited
    }
    assert not stray, f"未入表的 except Exception 现场: {stray}"


def test_counter_catches_synthetic_handlers():
    src = (
        "def f():\n"
        "    try:\n"
        "        pass\n"
        "    except Exception:\n"
        "        pass\n"
        "    try:\n"
        "        pass\n"
        "    except Exception as e:\n"
        "        print(e)\n"
        "    try:\n"
        "        pass\n"
        "    except ValueError:\n"
        "        pass\n"
    )
    tree = ast.parse(src)
    n = sum(
        1
        for h in ast.walk(tree)
        if isinstance(h, ast.ExceptHandler)
        and isinstance(h.type, ast.Name)
        and h.type.id == "Exception"
    )
    assert n == 2
