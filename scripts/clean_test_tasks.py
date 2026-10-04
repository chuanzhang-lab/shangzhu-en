#!/usr/bin/env python3
"""清理测试遗留在共享主库里的测试任务（E-02，移植自 shangzhu M-00 并本地化）。

背景：EN 仓测试历史上直写真实 PG（E-01 隔离落地前），且中英两仓曾共用
`shangzhu` 主库——任务表里积压测试残留（"New task" / "xhr-task" / "端到端" 等），
污染真实任务列表。E-01 隔离 + 分库落地后新残留不再产生，本脚本负责清历史存量。

与母版（shangzhu/scripts/clean_test_tasks.py）的两处本地化差异：
1. **目标库显式**：默认连 `shangzhu` 主库（残留所在），不是 EN 现在的默认库
   `shangzhu_en`。直接构造 PostgresStore(url)，**不走 get_store() 降级链**——
   清理工具若静默降级到文件 store，会把「清理」打到错误对象上。
2. **三条特征筛**（比母版两条更保守——误删真实数据风险定为高，宁漏勿错）：
   A 名称模式（测试指纹）/ B 同秒批量 ≥3（测试批量写入的时间指纹）/
   C 无真实业务消息（豁免检查）。A/B 命中后还需过 C：带真实对话的任务
   **只进报告、不动手**，留人工裁决。

产品默认名（"New task" / "新任务"）单列：真实用户懒得改名的任务也叫这个，
不能按测试指纹算——仅当零消息时才列入清理候选，有对话一律豁免。

用法（默认 dry-run，只列不动）：
    .venv/bin/python3 scripts/clean_test_tasks.py            # 预览将被清理的任务
    .venv/bin/python3 scripts/clean_test_tasks.py --apply    # 确认后软删（先 pg_dump 留档）

清理动作是软删（deleted_at 打点），走 store.delete_task()，与产品内删除一致，可恢复。
"""
import argparse
import os
import re
import subprocess
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from storage.local_store import PostgresStore, _DEFAULT_DB_URL  # noqa: E402

# 残留所在库 = 两仓曾共用的主库。从 _DEFAULT_DB_URL 同源推导主机/用户，
# 只换库名——不硬编码连接串（无凭据入库），也不用 EN 分库后的默认值。
MAIN_DB_URL = _DEFAULT_DB_URL.rsplit("/", 1)[0] + "/shangzhu"

# 测试写入用过的名字指纹（tests/ 历代用例 + 探测/冒烟脚本）。
# 注意 ^端到端$ 只收精确名："端到端咖啡店" 是真实任务（名字带业务后缀），不能误伤。
FINGERPRINT_RE = re.compile(
    r"^(New task|xhr-task|端到端|e2e-roundtrip|测试任务|改口测试|冒烟任务"
    r"|冒烟持久化验证|链路完整性探测|时间戳|断线自愈|旧名|新名|改个名|T|删"
    r"|probe-.*|r2-test-.*)$"
)
# 产品默认名：真实/测试都可能用，只作「零消息候选」的依据，不单独定罪
DEFAULT_NAME_RE = re.compile(r"^(New task|新任务)$")
BATCH_THRESHOLD = 3  # 同秒创建 >=3 个即视为测试批量


def classify(tasks):
    """三条特征筛，返回 (candidates, review, kept)。

    - candidates：A/B 命中且 C 豁免不触发（零消息）→ --apply 的清理对象；
    - review：A/B 命中但带消息 → 只列报告，人工裁决；
    - kept：其余（含默认名带真实对话的）→ 不碰。
    """
    by_second = defaultdict(list)
    for t in tasks:
        ts = str(t.get("created_at", ""))[:19]  # 精度到秒
        by_second[ts].append(t)

    candidates, review = [], []
    for t in tasks:
        name = t.get("name", "") or ""
        ts = str(t.get("created_at", ""))[:19]
        reasons = []
        if FINGERPRINT_RE.match(name):
            reasons.append(f"名称指纹: {name!r}")
        if DEFAULT_NAME_RE.match(name):
            reasons.append(f"产品默认名: {name!r}")
        if len(by_second[ts]) >= BATCH_THRESHOLD:
            reasons.append(f"同秒批量({len(by_second[ts])}个 @ {ts})")
        if not reasons:
            continue
        entry = (t, " + ".join(reasons))
        # C 豁免检查：有消息 = 可能是真实对话，只报不删
        if t.get("msg_count", 0) > 0:
            review.append(entry)
        else:
            candidates.append(entry)
    kept = [
        t for t in tasks
        if all(t["id"] != e[0]["id"] for e in candidates + review)
    ]
    return candidates, review, kept


def main():
    ap = argparse.ArgumentParser(
        description="清理测试遗留在 shangzhu 主库里的测试任务（默认 dry-run）"
    )
    ap.add_argument("--apply", action="store_true", help="真正软删（默认只预览）")
    ap.add_argument(
        "--db-url", default=MAIN_DB_URL,
        help="目标库连接串（默认两仓共用的 shangzhu 主库）",
    )
    args = ap.parse_args()

    # 直连指定库：不经 get_store()，杜绝「清理错对象」的降级路径
    store = PostgresStore(args.db_url)
    store.ping()
    tasks = store.list_tasks()
    for t in tasks:
        t["msg_count"] = len(store.get_messages(t["id"]))

    candidates, review, kept = classify(tasks)

    print(f"目标库: {type(store).__name__} ({store.target})")
    print(f"存活任务: {len(tasks)} 条 | 清理候选(零消息): {len(candidates)} 条 | "
          f"带消息待裁决: {len(review)} 条 | 保留: {len(kept)} 条\n")

    if candidates:
        print("── 清理候选（A/B 命中 + 零消息，--apply 将软删）──")
        for t, reason in candidates:
            print(f"  {str(t.get('created_at', '?'))[:19]}  {t['id']}  {reason}")
    if review:
        print("\n── 带消息待裁决（只报不删，人工确认后另行处理）──")
        for t, reason in review:
            first = (store.get_messages(t["id"]) or [{}])[0].get("content", "")[:50]
            print(f"  {str(t.get('created_at', '?'))[:19]}  {t['id']}  {reason}")
            print(f"      消息 {t['msg_count']} 条，首条: {first!r}")
    if kept:
        print("\n── 保留（无测试指纹）──")
        for t in kept:
            print(f"  {str(t.get('created_at', '?'))[:19]}  {t['id']}  "
                  f"{t.get('name', '')!r}（{t.get('msg_count', 0)} 条消息）")

    if not args.apply:
        print("\n[dry-run] 未做任何修改。确认无误后加 --apply 执行软删。")
        return 0

    if not candidates:
        print("\n[apply] 无清理候选，未做修改。")
        return 0

    # 软删前留档：复用 scripts/backup_db.sh（pg_dump + gzip，日期戳）
    dbname = store.target
    backup = subprocess.run(
        [os.path.join(ROOT, "scripts", "backup_db.sh"), dbname],
        capture_output=True, text=True,
    )
    if backup.returncode != 0:
        print(f"\n[apply] 备份失败，中止清理：{backup.stderr.strip()}",
              file=sys.stderr)
        return 1
    print(f"\n[apply] 备份留档完成：{backup.stdout.strip().splitlines()[-1]}")

    for t, _ in candidates:
        store.delete_task(t["id"])
    print(f"[apply] 已软删 {len(candidates)} 条测试残留"
          "（deleted_at 打点，可恢复）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
