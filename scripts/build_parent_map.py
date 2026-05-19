"""从盈亏表的参与地址出发,逐个调 getInfo(addr).referrer 拼出完整 parent_map。

输出: outputs/viz/parent_map.json  ->  {child_lower: parent_lower or None}

特性:
- 并发 16 路,Alchemy 加权 RPC 池(复用 pyrpc)
- 断点续传:每 200 条 flush 一次,意外中断后重跑会跳过已抓的
- 同时向上递归 referrer 的 referrer,直到命中 Genesis,确保祖先链完整
"""
from __future__ import annotations
import csv
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pyrpc

REFERRAL = "0x80472Ca15f2B6a3Ba29E18952e180D859C20e611"
GENESIS = "0x2fa1b0fe92286915a78ff073515debe50ab9c05d"
ZERO_ADDR = "0x" + "0" * 40
SEL_GET_INFO = "0xffdd5cf1"  # keccak256("getInfo(address)")[:4]

ROOT = Path(__file__).parent.parent
PNL_CSV = ROOT / "outputs/compensation/盈亏表.csv"
OUT_DIR = ROOT / "outputs/viz"
OUT_PATH = OUT_DIR / "parent_map.json"
FLUSH_EVERY = 200


def get_referrer(addr: str) -> str | None:
    """返回 addr 的上级 referrer (小写),没绑或 0x0 返回 None。"""
    data = SEL_GET_INFO + pyrpc.addr32(addr)
    h = pyrpc.eth_call(REFERRAL, data)
    if not h or h == "0x":
        return None
    h = h[2:]
    # 返回值布局: referrer(32) + directCount(32) + teamCount(32) + hasBound(32)
    referrer = "0x" + h[24:64]
    if referrer == ZERO_ADDR:
        return None
    return referrer.lower()


def load_existing() -> dict[str, str | None]:
    if OUT_PATH.exists():
        return json.loads(OUT_PATH.read_text())
    return {}


def flush(pm: dict[str, str | None]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(pm, ensure_ascii=False, indent=0))
    os.replace(tmp, OUT_PATH)


def load_participants() -> list[str]:
    """从盈亏表读出所有参与过 stake 的地址(小写)。"""
    addrs: list[str] = []
    with open(PNL_CSV) as f:
        for r in csv.DictReader(f):
            addrs.append(r["地址"].lower())
    return addrs


def fetch_batch(addrs: list[str], pm: dict[str, str | None]) -> int:
    """并发抓一批 addrs 的 referrer,写入 pm。返回新增条数。"""
    todo = [a for a in addrs if a not in pm]
    if not todo:
        return 0
    print(f"  待抓: {len(todo)} 个", flush=True)
    progress_state = {"n": 0, "t0": time.time()}

    def on_progress(done: int, total: int) -> None:
        if done % 100 == 0 or done == total:
            dt = time.time() - progress_state["t0"]
            rps = done / dt if dt > 0 else 0
            print(f"    {done}/{total}  ({rps:.1f}/s)", flush=True)

    results = pyrpc.parallel_map(get_referrer, todo, max_workers=16, progress=on_progress)
    added = 0
    for addr, ref in results.items():
        if isinstance(ref, Exception):
            print(f"    [WARN] {addr} → {ref}", flush=True)
            pm[addr] = None
        else:
            pm[addr] = ref
        added += 1
        if added % FLUSH_EVERY == 0:
            flush(pm)
    flush(pm)
    return added


def main() -> None:
    print(f"=== build_parent_map ===")
    print(f"输入: {PNL_CSV.relative_to(ROOT)}")
    print(f"输出: {OUT_PATH.relative_to(ROOT)}")

    pm = load_existing()
    print(f"已缓存: {len(pm)} 条")

    participants = load_participants()
    print(f"参与者总数: {len(participants)}")

    # Round 1: 抓所有参与者的 referrer
    print("\n[Round 1] 抓参与者的直接 referrer")
    fetch_batch(participants, pm)

    # Round 2~N: 把 referrer 但还没抓 referrer 的地址再抓一遍,直到链完整闭合到 Genesis
    round_idx = 2
    while True:
        new_targets: set[str] = set()
        for child, parent in pm.items():
            if parent and parent not in pm and parent != GENESIS:
                new_targets.add(parent)
        if not new_targets:
            break
        print(f"\n[Round {round_idx}] 抓中间祖先(无 stake 但是被 referrer 的)")
        fetch_batch(sorted(new_targets), pm)
        round_idx += 1
        if round_idx > 20:
            print("  ⚠ 超过 20 轮仍未闭合,停止")
            break

    # 闭合检查: 沿 child→parent 走,看是否能到 Genesis
    not_reaching = []
    for child in list(pm.keys())[:200]:
        cur = child
        for _ in range(100):
            cur = pm.get(cur)
            if cur is None or cur == GENESIS:
                break
        if cur != GENESIS:
            not_reaching.append(child)
    print(f"\n抽样 200 个,未能闭合到 Genesis 的: {len(not_reaching)}")
    if not_reaching:
        print(f"  示例: {not_reaching[:5]}")

    print(f"\n最终 parent_map 大小: {len(pm)}")
    print(f"写入: {OUT_PATH}")


if __name__ == "__main__":
    main()
