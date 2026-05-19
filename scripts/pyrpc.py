"""BSC JSON-RPC 工具：eth_call 并发 + 多 RPC 轮询 + 重试。"""
import itertools
import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable, Optional

import requests

import os
_alchemy = None
try:
    with open(os.path.join(os.path.dirname(__file__), "..", ".env")) as f:
        for line in f:
            if line.startswith("ALCHEMY_BNB_KEY="):
                _alchemy = line.split("=", 1)[1].strip()
                break
except Exception:
    pass

RPCS = []
if _alchemy:
    # Alchemy 优先（quota 充足时）— 多放几次以加权
    for _ in range(6):
        RPCS.append(_alchemy)
RPCS += [
    "https://bsc-dataseed.binance.org",
    "https://bsc-dataseed1.defibit.io",
    "https://bsc-dataseed1.ninicoin.io",
    "https://bsc-dataseed2.binance.org",
    "https://bsc-dataseed3.binance.org",
    "https://bsc.publicnode.com",
    "https://binance.llamarpc.com",
]

_rpc_iter = itertools.cycle(RPCS)
_lock = threading.Lock()


def pick_rpc() -> str:
    with _lock:
        return next(_rpc_iter)


_session_local = threading.local()


def _session():
    s = getattr(_session_local, "s", None)
    if s is None:
        s = requests.Session()
        from requests.adapters import HTTPAdapter
        s.mount("https://", HTTPAdapter(pool_connections=20, pool_maxsize=20))
        _session_local.s = s
    return s


def eth_call(to: str, data: str, retries: int = 8, block: str = "latest") -> str:
    last_err = None
    for attempt in range(retries):
        rpc = pick_rpc()
        try:
            r = _session().post(
                rpc,
                json={
                    "jsonrpc": "2.0",
                    "method": "eth_call",
                    "params": [{"to": to, "data": data}, block],
                    "id": 1,
                },
                timeout=30,
            )
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}"
                time.sleep(0.2 + random.random() * 0.6)
                continue
            body = r.json()
            if "result" in body:
                return body["result"]
            if "error" in body:
                last_err = body["error"]
        except Exception as e:
            last_err = str(e)[:120]
        time.sleep(0.2 + random.random() * 0.6)
    raise RuntimeError(f"eth_call failed after {retries} retries: {last_err}")


def get_logs(params: dict, retries: int = 8) -> list:
    last_err = None
    for attempt in range(retries):
        rpc = pick_rpc()
        try:
            r = _session().post(
                rpc,
                json={
                    "jsonrpc": "2.0",
                    "method": "eth_getLogs",
                    "params": [params],
                    "id": 1,
                },
                timeout=60,
            )
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}"
                time.sleep(0.3)
                continue
            body = r.json()
            if "result" in body:
                return body["result"]
            if "error" in body:
                last_err = body["error"]
        except Exception as e:
            last_err = str(e)[:120]
        time.sleep(0.3 + random.random() * 0.6)
    raise RuntimeError(f"eth_getLogs failed after {retries} retries: {last_err}")


def addr32(addr: str) -> str:
    return addr.lower().replace("0x", "").rjust(64, "0")


def u32(n: int) -> str:
    return f"{n:064x}"


def decode_addr(hexstr: str) -> str:
    return "0x" + hexstr[-40:]


def decode_addr_array(result_hex: str) -> list:
    h = result_hex[2:] if result_hex.startswith("0x") else result_hex
    if len(h) < 128:
        return []
    length = int(h[64:128], 16)
    addrs = []
    for i in range(length):
        chunk = h[128 + i * 64 : 128 + (i + 1) * 64]
        addrs.append(decode_addr(chunk))
    return addrs


def parallel_map(fn: Callable, items: Iterable, max_workers: int = 12, progress: Optional[Callable] = None):
    results = {}
    items = list(items)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_to_item = {ex.submit(fn, it): it for it in items}
        done = 0
        for fut in as_completed(future_to_item):
            it = future_to_item[fut]
            try:
                results[it] = fut.result()
            except Exception as e:
                results[it] = e
            done += 1
            if progress:
                progress(done, len(items))
    return results
