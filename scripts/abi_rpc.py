"""共用模块:用 urllib 直连 Infura/公共节点做 eth_call,简易 ABI 编解码。"""
import urllib.request, json, time, subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
ENV = (ROOT / ".env").read_text()
INFURA = next(l.split("=",1)[1].strip() for l in ENV.splitlines()
              if l.startswith("INFURA_BNB_KEY="))
ALCHEMY = next((l.split("=",1)[1].strip() for l in ENV.splitlines()
                if l.startswith("ALCHEMY_BNB_KEY=")), None)
PUBLIC = "https://bsc-dataseed.binance.org"
RPCS = [INFURA] + ([ALCHEMY] if ALCHEMY else []) + [PUBLIC,
        "https://bsc-dataseed1.defibit.io",
        "https://bsc-dataseed1.ninicoin.io",
        "https://bsc.publicnode.com"]

_sel_cache = {}
def selector(sig: str) -> str:
    if sig in _sel_cache: return _sel_cache[sig]
    out = subprocess.run(["cast","keccak",sig], capture_output=True, text=True).stdout.strip()
    s = out[:10]
    _sel_cache[sig] = s
    return s

def encode_address(a: str) -> str:
    return a.lower().replace("0x","").rjust(64, "0")

def encode_uint(n: int) -> str:
    return f"{n:064x}"

def decode_address(hex32: str) -> str:
    return "0x" + hex32[-40:]

def decode_uint(hex32: str) -> int:
    return int(hex32, 16)

def decode_address_array(data_hex: str):
    """ABI dynamic address[] return: [offset (0x20)][length][addr...]"""
    h = data_hex[2:] if data_hex.startswith("0x") else data_hex
    # offset is at 0..64; length at offset*2 .. offset*2+64
    offset = int(h[0:64], 16) * 2
    length = int(h[offset:offset+64], 16)
    out = []
    base = offset + 64
    for i in range(length):
        slot = h[base + i*64 : base + (i+1)*64]
        out.append("0x" + slot[-40:])
    return out

def rpc(method, params, retries=6):
    last_err = None
    for attempt in range(retries):
        url = RPCS[attempt % len(RPCS)]
        try:
            req = urllib.request.Request(url,
                data=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode(),
                headers={"content-type":"application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read())
                if "error" in d:
                    raise RuntimeError(d["error"])
                if "result" not in d:
                    raise RuntimeError(f"no result: {d}")
                return d["result"]
        except Exception as e:
            last_err = e
            time.sleep(0.5 + attempt * 0.5)
    raise last_err

def eth_call(to, data, block="latest"):
    blk = block if isinstance(block, str) else hex(block)
    return rpc("eth_call", [{"to": to, "data": data}, blk])

def call_func(to, sig, args_hex, block="latest"):
    data = selector(sig) + args_hex
    return eth_call(to, data, block)

# 高层封装: Referral 合约
def get_info(referral, addr, block="latest"):
    """returns (referrer, directCount, teamCount, hasBound)"""
    h = call_func(referral, "getInfo(address)", encode_address(addr), block)
    h = h[2:]
    return (decode_address(h[0:64]),
            decode_uint(h[64:128]),
            decode_uint(h[128:192]),
            bool(decode_uint(h[192:256])))

def get_referral_of(referral, addr, block="latest"):
    h = call_func(referral, "getReferral(address)", encode_address(addr), block)
    return decode_address(h[2:])

def get_direct_count(referral, addr, block="latest"):
    h = call_func(referral, "getDirectCount(address)", encode_address(addr), block)
    return decode_uint(h[2:])

def get_team_count(referral, addr, block="latest"):
    h = call_func(referral, "getTeamCount(address)", encode_address(addr), block)
    return decode_uint(h[2:])

def get_downlines(referral, addr, num, offset, block="latest"):
    args = encode_address(addr) + encode_uint(num) + encode_uint(offset)
    h = call_func(referral, "getDownlines(address,uint256,uint256)", args, block)
    return decode_address_array(h)

# Staking 合约
def get_team_kpi(staking, addr, block="latest"):
    h = call_func(staking, "getTeamKPI(address)", encode_address(addr), block)
    return decode_uint(h[2:])

def get_user_balance(staking, addr, block="latest"):
    """staking.balances[addr] - 用户名义本金"""
    h = call_func(staking, "balances(address)", encode_address(addr), block)
    return decode_uint(h[2:])
