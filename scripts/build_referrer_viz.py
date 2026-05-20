"""把 parent_map + 盈亏表 → 单文件 HTML BubbleMap。

输入:
  outputs/viz/parent_map.json
  outputs/compensation/盈亏表.csv

输出:
  outputs/viz/graph.json      (中间数据,便于复用)
  outputs/viz/bubblemap.html  (单文件,直接 open 即可)

布局:Canvas + D3 v7 force-directed,Genesis 锁中心,
节点按综合盈亏配色,大小按累计 stake 取对数,Top 50 + Genesis 默认显示标签。
"""
from __future__ import annotations
import csv
import json
import math
import os
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
PARENT_MAP = ROOT / "outputs/viz/parent_map.json"
PNL_CSV = ROOT / "outputs/compensation/盈亏表.csv"
WALLET_DETAIL_CSV = ROOT / "reports/全网/全部地址明细.csv"  # 含 取出 B / 取出次数,数据基于 Transfer 事件
DEFAULT_ALIASES_JSON = ROOT / "outputs/viz/biying_aliases.json"  # 内置项目默认别名(从 raw/底池监控/地址.csv 生成)
OUT_DIR = ROOT / "outputs/viz"
GRAPH_JSON = OUT_DIR / "graph.json"
HTML = OUT_DIR / "bubblemap.html"

GENESIS = "0x2fa1b0fe92286915a78ff073515debe50ab9c05d"


def load_pnl() -> dict[str, dict]:
    """读 v6 盈亏表(已含 钱包总收款 / 分润奖励 / 钱包收款笔数 三列)。
    v6 cutoff: stake ≤ 5-12 23:59;unstake/钱包收款 ≤ 5-19 23:59 CST。
    已实现盈亏 = 钱包总收款 − stake (含分润口径)。
    """
    out: dict[str, dict] = {}
    with open(PNL_CSV) as f:
        for r in csv.DictReader(f):
            a = r["地址"].lower()
            stake = float(r["累计stake金额(USDT)"])
            unstake = float(r["累计unstake到账(USDT)"])
            wallet_in_total = float(r["钱包总收款(USDT)"])
            wallet_in_count = int(r["钱包收款笔数"])
            commission_etc = float(r["分润奖励(USDT)"])
            out[a] = {
                "stake": stake,
                "unstake": unstake,
                "pnl": float(r["已实现盈亏(USDT)"]),       # v6: = 钱包总收款 - stake
                "pnl_after_comp": float(r["综合盈亏(USDT)"]),  # v6: 含分润 + 赔付假设
                "in_stake": float(r["在stake本金(USDT)"]),
                "stolen": float(r["疑似被盗本金(USDT)"]),
                "owed": float(r["应赔付总额(USDT)"]),
                "wallet_in_total": wallet_in_total,
                "wallet_in_count": wallet_in_count,
                "commission_etc": commission_etc,
                # 钱包净现金流(=已实现盈亏,只在 v6 等价;保留字段名以兼容 HTML 模板)
                "net_cashflow": wallet_in_total - stake,
            }
    return out


def build_graph(parent_map: dict[str, str | None], pnl: dict[str, dict]) -> dict:
    # 节点 = 参与过 stake 的所有地址 + Genesis + 所有 ancestor(即使没参与 stake,作为骨架)
    participants = set(pnl.keys())
    ancestors = set()
    for child in participants:
        cur = child
        for _ in range(80):
            p = parent_map.get(cur)
            if not p or p == GENESIS or p in participants or p in ancestors:
                break
            ancestors.add(p)
            cur = p
    node_set = participants | ancestors | {GENESIS}

    # 计算每个节点的 depth(到 Genesis 的距离)
    depth_cache: dict[str, int] = {GENESIS: 0}

    def get_depth(addr: str) -> int:
        if addr in depth_cache:
            return depth_cache[addr]
        path: list[str] = []
        cur = addr
        while cur not in depth_cache:
            path.append(cur)
            p = parent_map.get(cur)
            if not p:
                # orphan
                depth_cache[cur] = -1
                cur = None
                break
            cur = p
        if cur is None:
            for x in path:
                depth_cache[x] = -1
            return depth_cache[addr]
        base = depth_cache[cur]
        for i, x in enumerate(reversed(path)):
            depth_cache[x] = base + i + 1
        return depth_cache[addr]

    for a in node_set:
        get_depth(a)

    # Top 50 by 累计 stake (仅参与者中)
    top50 = sorted(
        ((a, pnl[a]["stake"]) for a in participants),
        key=lambda x: -x[1],
    )[:50]
    top50_set = {a for a, _ in top50}

    nodes = []
    for a in node_set:
        p = pnl.get(a, {})
        node = {
            "id": a,
            "parent": parent_map.get(a) if a != GENESIS else None,
            "depth": depth_cache.get(a, -1),
            "is_genesis": a == GENESIS,
            "is_participant": a in participants,
            "is_top50": a in top50_set,
            "stake": p.get("stake", 0.0),
            "unstake": p.get("unstake", 0.0),
            "pnl": p.get("pnl", 0.0),
            "pnl_after_comp": p.get("pnl_after_comp", 0.0),
            "in_stake": p.get("in_stake", 0.0),
            "stolen": p.get("stolen", 0.0),
            "owed": p.get("owed", 0.0),
            "wallet_in_total": p.get("wallet_in_total", 0.0),
            "wallet_in_count": p.get("wallet_in_count", 0),
            "commission_etc": p.get("commission_etc", 0.0),
            "net_cashflow": p.get("net_cashflow", 0.0),
        }
        nodes.append(node)

    edges = []
    for a in node_set:
        if a == GENESIS:
            continue
        p = parent_map.get(a)
        if p and p in node_set:
            edges.append({"src": a, "dst": p})

    return {
        "root": GENESIS,
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "total_nodes": len(nodes),
            "participants": len(participants),
            "ancestors": len(ancestors),
            "edges": len(edges),
            "top50_threshold_stake": top50[-1][1] if top50 else 0,
        },
    }


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>必赢全网 referrer BubbleMap</title>
<script src="https://d3js.org/d3.v7.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/xlsx@0.18.5/dist/xlsx.full.min.js"></script>
<style>
  html, body { margin:0; padding:0; background:#0a0a0a; color:#e5e5e5; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; overflow:hidden; }
  #header { position:fixed; top:0; left:0; right:0; padding:10px 16px; background:rgba(10,10,10,0.85); border-bottom:1px solid #222; z-index:10; display:flex; align-items:center; gap:16px; font-size:13px; }
  #header h1 { font-size:14px; margin:0; font-weight:600; color:#fafafa; white-space:nowrap; }
  #header .legend { display:flex; gap:14px; align-items:center; flex-wrap:wrap; }
  #header .legend .dot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:4px; vertical-align:middle; }
  #header .legend span { color:#bbb; }
  #search { background:#1a1a1a; border:1px solid #333; color:#eee; padding:5px 10px; border-radius:4px; width:380px; font-size:12px; font-family:monospace; }
  #search:focus { outline:none; border-color:#666; }
  #search.no-hit { border-color:#7c2d2d; }
  #meta { margin-left:auto; color:#888; font-size:11px; white-space:nowrap; }
  #canvas { display:block; cursor:grab; }
  #canvas:active { cursor:grabbing; }
  #tooltip { position:fixed; background:rgba(20,20,20,0.97); border:1px solid #444; border-radius:6px; padding:10px 12px; pointer-events:none; font-size:12px; max-width:340px; line-height:1.5; display:none; z-index:20; box-shadow:0 4px 20px rgba(0,0,0,0.6); }
  #tooltip .addr { font-family:monospace; color:#fafafa; word-break:break-all; font-size:11px; }
  #tooltip .row { color:#bbb; }
  #tooltip .row b { color:#fafafa; font-weight:500; }
  #status { position:fixed; bottom:8px; left:12px; color:#666; font-size:11px; font-family:monospace; }
  .pnl-pos { color:#22c55e; }
  .pnl-neg { color:#ef4444; }
  .pnl-zero { color:#9ca3af; }

  /* —— 右侧面板 —— */
  #panel { position:fixed; top:48px; right:0; bottom:0; width:440px; background:rgba(15,15,15,0.96); border-left:1px solid #222; overflow-y:auto; padding:14px 16px 60px; font-size:12px; z-index:15; display:none; box-shadow:-10px 0 30px rgba(0,0,0,0.6); }
  #panel.open { display:block; }
  #panel-close { position:absolute; top:8px; right:10px; color:#888; font-size:18px; cursor:pointer; background:none; border:none; padding:4px 8px; }
  #panel-close:hover { color:#fff; }
  #panel .head { padding-right:30px; border-bottom:1px solid #222; padding-bottom:12px; margin-bottom:10px; }
  #panel .head .addr { font-family:monospace; font-size:12px; word-break:break-all; color:#fafafa; padding:6px 8px; background:#1a1a1a; border-radius:4px; user-select:all; }
  #panel .head .role { color:#bbb; margin-top:6px; font-size:11px; }
  #panel .head .stat { display:grid; grid-template-columns:auto 1fr; gap:4px 12px; margin-top:8px; }
  #panel .head .stat b { color:#fafafa; font-weight:500; }
  #panel .head .stat span { color:#bbb; }
  #panel .head .stat span.right { text-align:right; font-variant-numeric:tabular-nums; }
  #panel .head .actions { margin-top:10px; display:flex; gap:8px; }
  #panel .head .actions button { background:#1d4ed8; color:#fff; border:none; padding:6px 12px; border-radius:4px; font-size:12px; cursor:pointer; font-weight:500; }
  #panel .head .actions button:hover { background:#2563eb; }
  #panel .head .actions button.secondary { background:#1f2937; }
  #panel .head .actions button.secondary:hover { background:#374151; }
  #panel details.umbrella .stat { display:grid; grid-template-columns:auto 1fr; gap:4px 12px; padding:4px 2px; font-size:12px; }
  #panel details.umbrella .stat b { color:#fafafa; font-weight:500; }
  #panel details.umbrella .stat span.right { text-align:right; font-variant-numeric:tabular-nums; color:#bbb; }
  #panel details { margin-top:10px; border-top:1px solid #1f1f1f; padding-top:8px; }
  #panel summary { cursor:pointer; color:#fafafa; font-weight:500; font-size:13px; outline:none; padding:4px 0; user-select:none; }
  #panel summary:hover { color:#fde047; }
  #panel summary .count { color:#888; font-weight:normal; font-size:11px; margin-left:6px; }
  #panel ul.chain, #panel ul.tree { list-style:none; padding-left:0; margin:6px 0; }
  #panel ul.tree { padding-left:14px; border-left:1px dashed #2a2a2a; }
  #panel li.node-row { padding:3px 4px; border-radius:3px; cursor:pointer; display:flex; align-items:center; gap:6px; font-family:monospace; font-size:11px; line-height:1.5; }
  #panel li.node-row:hover { background:#1f1f1f; }
  #panel li.node-row.is-current { background:#2a2a14; color:#fde047; }
  #panel li.node-row .depth { color:#666; min-width:36px; }
  #panel li.node-row .addr { color:#ddd; flex:1; }
  #panel li.node-row .pnl { font-size:10px; padding:1px 6px; border-radius:3px; font-family:-apple-system,sans-serif; }
  #panel li.node-row .pnl.pos { background:#0f2a18; color:#22c55e; }
  #panel li.node-row .pnl.neg { background:#2a1010; color:#ef4444; }
  #panel li.node-row .pnl.grey { background:#1f1f1f; color:#9ca3af; }
  #panel li.node-row .toggle { color:#666; font-family:-apple-system,sans-serif; cursor:pointer; min-width:14px; user-select:none; }
  #panel li.node-row .toggle:hover { color:#fff; }
  #panel li.node-row .leaf-spacer { min-width:14px; }
  #panel .hint { color:#666; font-size:11px; padding:4px 0; font-style:italic; }
  #panel .empty { color:#888; padding:8px 0; font-style:italic; font-size:11px; }

  /* —— alias —— */
  #panel .head .alias-row { display:flex; align-items:center; gap:6px; margin-bottom:6px; }
  #panel .head .alias-row input { flex:1; background:#1a1a1a; color:#fde047; border:1px solid #444; border-radius:3px; padding:4px 8px; font-size:14px; font-weight:500; }
  #panel .head .alias-row input:focus { outline:none; border-color:#fde047; }
  #panel .head .alias-row .alias-tag { flex:1; color:#fde047; font-weight:600; font-size:14px; padding:4px 0; }
  #panel .head .alias-row button { background:none; border:none; color:#888; cursor:pointer; font-size:13px; padding:2px 6px; border-radius:3px; }
  #panel .head .alias-row button:hover { color:#fff; background:#1f1f1f; }
  #panel li.node-row .alias { color:#fde047; font-weight:500; font-family:-apple-system,sans-serif; margin-right:6px; }

  /* —— alias manager 弹窗 —— */
  #alias-btn { background:#1f2937; border:1px solid #444; color:#eee; padding:5px 10px; border-radius:4px; font-size:12px; cursor:pointer; }
  #alias-btn:hover { background:#374151; }
  #alias-modal { position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.7); z-index:30; display:none; align-items:center; justify-content:center; }
  #alias-modal.open { display:flex; }
  #alias-modal .box { background:#111; border:1px solid #333; border-radius:8px; padding:18px 20px; width:540px; max-height:80vh; overflow-y:auto; font-size:13px; box-shadow:0 20px 60px rgba(0,0,0,0.7); }
  #alias-modal h2 { margin:0 0 10px 0; font-size:15px; color:#fafafa; }
  #alias-modal .row { display:flex; gap:8px; padding:6px 0; border-bottom:1px solid #1f1f1f; align-items:center; }
  #alias-modal .row input { background:#1a1a1a; border:1px solid #333; color:#eee; padding:5px 8px; border-radius:3px; font-size:12px; font-family:monospace; }
  #alias-modal .row input.addr { flex:2; }
  #alias-modal .row input.name { flex:1; color:#fde047; font-family:-apple-system,sans-serif; }
  #alias-modal .row button { background:none; border:none; color:#888; cursor:pointer; padding:4px 6px; }
  #alias-modal .row button:hover { color:#ef4444; }
  #alias-modal .actions { margin-top:14px; display:flex; gap:8px; }
  #alias-modal .actions button { background:#1d4ed8; color:#fff; border:none; padding:6px 14px; border-radius:4px; font-size:12px; cursor:pointer; }
  #alias-modal .actions button.secondary { background:#1f2937; }
  #alias-modal .actions button.danger { background:#7c2d2d; }
  #alias-modal .empty-tip { color:#888; padding:14px 0; text-align:center; font-style:italic; }
</style>
</head>
<body>
<div id="header">
  <h1>必赢 全网 referrer BubbleMap</h1>
  <div class="legend">
    <span><span class="dot" style="background:#22c55e"></span>已盈利</span>
    <span><span class="dot" style="background:#ef4444"></span>已亏损 (待赔付)</span>
    <span><span class="dot" style="background:#6b7280"></span>疑似被盗</span>
    <span><span class="dot" style="background:#fde047"></span>Genesis</span>
    <span style="color:#666">|</span>
    <span><span class="dot" style="background:#a855f7"></span>上线链</span>
    <span><span class="dot" style="background:#fbbf24"></span>下线树</span>
  </div>
  <input id="search" placeholder="粘贴地址 / 输入别名…" autocomplete="off" />
  <button id="alias-btn" title="管理地址别名">📒 别名</button>
  <div id="meta"></div>
</div>
<canvas id="canvas"></canvas>
<div id="tooltip"></div>
<div id="status"></div>

<aside id="panel">
  <button id="panel-close" title="关闭">×</button>
  <div id="panel-body"></div>
</aside>

<div id="alias-modal">
  <div class="box">
    <h2>📒 地址别名管理 <span style="color:#888;font-size:11px;font-weight:normal">(🔒 私加 = 只对你可见 / 📒 内置 = 所有人可见)</span></h2>
    <div id="alias-list"></div>
    <div class="row" style="border-top:1px solid #333;margin-top:8px;padding-top:10px">
      <input class="addr" id="alias-new-addr" placeholder="0x... 完整地址" />
      <input class="name" id="alias-new-name" placeholder="起个名字 如 紫悦 / 牛逼哥" />
      <button id="alias-new-add" title="添加" style="color:#22c55e">+ 添加</button>
    </div>
    <div class="actions">
      <button id="alias-export">⬇ 导出 JSON</button>
      <button class="secondary" id="alias-import">⬆ 导入 JSON</button>
      <button class="danger" id="alias-clear">🗑 清空全部</button>
      <button class="secondary" id="alias-close" style="margin-left:auto">关闭</button>
    </div>
  </div>
</div>

<script type="application/json" id="graphdata">__GRAPH_JSON__</script>
<script>
const data = JSON.parse(document.getElementById("graphdata").textContent);
const canvas = document.getElementById("canvas");
const ctx = canvas.getContext("2d");
const tooltip = document.getElementById("tooltip");
const statusEl = document.getElementById("status");
const meta = document.getElementById("meta");
const panel = document.getElementById("panel");
const panelBody = document.getElementById("panel-body");
const searchInput = document.getElementById("search");

document.getElementById("meta").textContent =
  "参与者 " + data.stats.participants + " · 边 " + data.stats.edges + " · 生成 " + (data.generated || "");

// —— 别名(localStorage)——
const ALIAS_KEY = "biying_bubblemap_aliases";
const USER_FLAGS_KEY = "biying_bubblemap_user_alias_flags";  // 标记哪些是"用户私加/改的"
// 项目内置的默认别名(从 raw/底池监控/地址.csv 生成,build 时烘焙进 HTML;所有人可见)
const DEFAULT_ALIASES = __DEFAULT_ALIASES__;
let aliases = {};
let userFlags = new Set();  // 用户私改/私加过的 addr 集合
function loadAliases() {
  try { aliases = JSON.parse(localStorage.getItem(ALIAS_KEY) || "{}") || {}; }
  catch (e) { aliases = {}; }
  try { userFlags = new Set(JSON.parse(localStorage.getItem(USER_FLAGS_KEY) || "[]")); }
  catch (e) { userFlags = new Set(); }
  // 自动补齐内置默认别名(用户已删除/改过的不覆盖)
  let added = 0;
  for (const [addr, name] of Object.entries(DEFAULT_ALIASES)) {
    if (userFlags.has(addr)) continue;  // 用户动过这个,不强加默认
    if (aliases[addr] !== name) { aliases[addr] = name; added++; }
  }
  if (added > 0) saveAliases();
  console.log("[alias] 内置 " + Object.keys(DEFAULT_ALIASES).length + " · 用户私加 " + userFlags.size + " · 共 " + Object.keys(aliases).length);
}
function saveAliases() {
  localStorage.setItem(ALIAS_KEY, JSON.stringify(aliases));
  localStorage.setItem(USER_FLAGS_KEY, JSON.stringify([...userFlags]));
}
function getAlias(addr) {
  return addr ? (aliases[addr.toLowerCase()] || null) : null;
}
function isUserAlias(addr) {
  // 是否是"用户私改/私加":1) 在 userFlags 集合里 → 是;2) 在 DEFAULT_ALIASES 但被改了 → 也算
  if (!addr) return false;
  const k = addr.toLowerCase();
  if (userFlags.has(k)) return true;
  const def = DEFAULT_ALIASES[k];
  if (def != null && aliases[k] != null && aliases[k] !== def) return true;
  return false;
}
function aliasColor(addr) {
  return isUserAlias(addr) ? "#22d3ee" /* 青 cyan = 用户私加 */ : "#fde047" /* 黄 yellow = 内置 */;
}
function setAlias(addr, name) {
  const k = addr.toLowerCase();
  const v = (name || "").trim();
  if (v) aliases[k] = v;
  else delete aliases[k];
  userFlags.add(k);   // 用户每一次写入都标记
  saveAliases();
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function aliasLabel(addr, opts) {
  // 用于面板/tooltip 文字。opts: {short:true, prefixOnly:false}
  const a = getAlias(addr);
  const isGenesis = addr === data.root;
  if (isGenesis && !a) return "GENESIS";
  const shortAddr = (addr.slice(0, 6) + "…" + addr.slice(-4));
  const fullOrShort = opts && opts.short ? shortAddr : addr;
  if (a) {
    if (opts && opts.prefixOnly) return a;
    return a + " <" + fullOrShort + ">";
  }
  return fullOrShort;
}
loadAliases();

let W = window.innerWidth, H = window.innerHeight;
function sizeCanvas() {
  canvas.width = W * window.devicePixelRatio;
  canvas.height = H * window.devicePixelRatio;
  canvas.style.width = W + "px";
  canvas.style.height = H + "px";
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
}
sizeCanvas();

// 视觉
function nodeRadius(n) {
  if (n.is_genesis) return 28;
  if (!n.is_participant) return 1.8;
  const r = 3 + Math.log10(n.stake + 10) * 2.5;
  return n.is_top50 ? r * 1.5 : r;
}
function nodeColor(n) {
  if (n.is_genesis) return "#fde047";
  if (!n.is_participant) return "#374151";
  if (n.stolen > 0 && n.in_stake <= 0) return "#6b7280";
  if (n.pnl > 0.01) return "#22c55e";
  if (n.pnl < -0.01) return "#ef4444";
  return "#9ca3af";
}
function edgeColor(child) {
  if (!child.is_participant) return "rgba(80,80,80,0.18)";
  if (child.pnl > 0.01) return "rgba(34,197,94,0.18)";
  if (child.pnl < -0.01) return "rgba(239,68,68,0.18)";
  return "rgba(160,160,160,0.18)";
}
function pnlClass(v) {
  if (v > 0.01) return "pos";
  if (v < -0.01) return "neg";
  return "grey";
}
function fmt(v) { return v.toLocaleString("en-US", { maximumFractionDigits: 2 }); }
function shortAddr(a) { return a.slice(0, 6) + "…" + a.slice(-4); }

// 构图
const nodeById = new Map(data.nodes.map(n => [n.id, n]));
const links = data.edges
  .filter(e => nodeById.has(e.src) && nodeById.has(e.dst))
  .map(e => ({ source: e.src, target: e.dst, child: nodeById.get(e.src) }));

// 反向索引: parent → [children]
const childrenMap = new Map();
for (const n of data.nodes) {
  if (n.parent) {
    if (!childrenMap.has(n.parent)) childrenMap.set(n.parent, []);
    childrenMap.get(n.parent).push(n.id);
  }
}

// 固定 Genesis
const genesis = nodeById.get(data.root);
if (genesis) { genesis.fx = 0; genesis.fy = 0; }

const simulation = d3.forceSimulation(data.nodes)
  .force("link", d3.forceLink(links).id(d => d.id).distance(d => 18 + (d.target.is_genesis ? 12 : 0)).strength(0.55))
  .force("charge", d3.forceManyBody().strength(d => d.is_genesis ? -2200 : (d.is_top50 ? -90 : -22)).distanceMax(380))
  .force("center", d3.forceCenter(0, 0).strength(0.04))
  .force("collide", d3.forceCollide().radius(d => nodeRadius(d) + 1.2).strength(0.85))
  .velocityDecay(0.45)
  .alphaMin(0.005)
  .alpha(1);

// —— 高亮状态(必须在 draw 被首次调用前声明,否则 TDZ 报错) ——
let selected = null;
let highlightUp = new Set();
let highlightDown = new Set();
const labelBoxes = [];  // 画布上文字标签的世界坐标命中框,由 draw() 每次刷新

// —— 缩放/平移: 同一个 zoom 行为对象 ——
let transform = d3.zoomIdentity.translate(W / 2, H / 2).scale(0.85);
const zoomBehavior = d3.zoom()
  .scaleExtent([0.04, 8])
  .on("zoom", (event) => {
    transform = event.transform;
    draw();
  });
const cSel = d3.select(canvas);
cSel.call(zoomBehavior);
cSel.call(zoomBehavior.transform, transform);

function panToNode(id, targetScale) {
  const n = nodeById.get(id);
  if (!n || n.x === undefined) return;
  const k = targetScale != null ? targetScale : Math.max(transform.k, 1.5);
  const newT = d3.zoomIdentity.translate(W / 2 - n.x * k, H / 2 - n.y * k).scale(k);
  cSel.transition().duration(750).call(zoomBehavior.transform, newT);
}

function ancestorsOf(addr, maxDepth = 100) {
  const out = [];
  let cur = addr;
  for (let i = 0; i < maxDepth; i++) {
    const n = nodeById.get(cur);
    if (!n || !n.parent) break;
    out.push(n.parent);
    cur = n.parent;
  }
  return out;
}

function descendantsOf(addr, maxDepth = 30) {
  // BFS
  const seen = new Set();
  const layers = [];  // [[depth1 ids], [depth2 ids], ...]
  let frontier = [addr];
  for (let d = 0; d < maxDepth; d++) {
    const next = [];
    for (const cur of frontier) {
      const kids = childrenMap.get(cur) || [];
      for (const k of kids) {
        if (seen.has(k)) continue;
        seen.add(k);
        next.push(k);
      }
    }
    if (!next.length) break;
    layers.push(next);
    frontier = next;
  }
  return { layers, total: seen.size };
}

function selectNode(addr) {
  if (!nodeById.has(addr)) return;
  selected = addr;
  highlightUp = new Set(ancestorsOf(addr));
  const desc = descendantsOf(addr, 100);
  highlightDown = new Set();
  for (const layer of desc.layers) for (const x of layer) highlightDown.add(x);
  renderPanel(addr, desc);
  panel.classList.add("open");
  panToNode(addr);
  draw();
}

// —— 伞下汇总(焦点 + 全部下线)——
function umbrellaAggregate(focusAddr, desc) {
  const acc = {
    count_total: 1,
    count_profit: 0,
    count_loss: 0,
    count_stolen: 0,
    stake: 0, unstake: 0, in_stake: 0, stolen: 0, owed: 0,
    realized_pnl: 0, pnl_after_comp: 0,
    wallet_in_total: 0, commission_etc: 0, net_cashflow: 0,
  };
  const tally = (n) => {
    acc.stake += n.stake || 0;
    acc.unstake += n.unstake || 0;
    acc.in_stake += n.in_stake || 0;
    acc.stolen += n.stolen || 0;
    acc.owed += n.owed || 0;
    acc.realized_pnl += n.pnl || 0;
    acc.pnl_after_comp += n.pnl_after_comp || 0;
    acc.wallet_in_total += n.wallet_in_total || 0;
    acc.commission_etc += n.commission_etc || 0;
    acc.net_cashflow += n.net_cashflow || 0;
    if (n.stolen > 0 && n.in_stake <= 0) acc.count_stolen++;
    else if (n.pnl > 0.01) acc.count_profit++;
    else if (n.pnl < -0.01) acc.count_loss++;
  };
  tally(nodeById.get(focusAddr));
  for (const layer of desc.layers) {
    for (const a of layer) {
      tally(nodeById.get(a));
      acc.count_total++;
    }
  }
  return acc;
}

function clearSelection() {
  selected = null;
  highlightUp = new Set();
  highlightDown = new Set();
  panel.classList.remove("open");
  searchInput.classList.remove("no-hit");
  draw();
}

document.getElementById("panel-close").addEventListener("click", clearSelection);

function renderPanel(addr, desc) {
  const n = nodeById.get(addr);
  const role = n.is_genesis ? "GENESIS (根)" : (n.is_participant ? (n.is_top50 ? "Top 50 大户" : "参与者") : "祖先(未参与 stake)");
  const pnlCls = pnlClass(n.pnl);
  const ancestors = ancestorsOf(addr);
  const curAlias = getAlias(addr);

  const aliasIsUser = isUserAlias(addr);
  const aliasTagColor = aliasColor(addr);
  const aliasSourceTip = curAlias ? (aliasIsUser ? "🔒 私加(只你可见)" : "📒 内置(所有人可见)") : "";

  let html = `<div class="head">
    <div class="alias-row" id="alias-row-host">
      ${curAlias
        ? `<span class="alias-tag" style="color:${aliasTagColor}">${aliasIsUser ? "🔒" : "📒"} ${escapeHtml(curAlias)}</span>
           <span style="color:#666;font-size:10px">${aliasSourceTip}</span>
           <button data-alias-action="edit" title="改名">✏️</button>
           <button data-alias-action="remove" title="移除别名">✕</button>`
        : `<button data-alias-action="add" title="给这个地址起个名(只你自己可见)" style="color:#22d3ee;border:1px dashed #555;padding:4px 10px">+ 起个名字(私加)</button>`}
    </div>
    <div class="addr">${addr}</div>
    <div class="role">${role} · 深度 ${n.depth}</div>`;
  if (n.is_participant) {
    html += `<div class="stat">
      <b>累计 stake:</b><span class="right">${fmt(n.stake)} U</span>
      <b>累计 unstake:</b><span class="right">${fmt(n.unstake)} U <span style="color:#666">(合约口径)</span></span>
      <b>钱包总收款:</b><span class="right">${fmt(n.wallet_in_total)} U <span style="color:#666">(${n.wallet_in_count} 笔)</span></span>
      <b>&nbsp;&nbsp;其中分润奖励:</b><span class="right">${fmt(n.commission_etc)} U</span>
      <b>在押本金:</b><span class="right">${fmt(n.in_stake)} U</span>
      <b>疑似被盗本金:</b><span class="right">${fmt(n.stolen)} U</span>
      <b>应赔付总额:</b><span class="right">${fmt(n.owed)} U</span>
      <b>已实现盈亏 <span style="color:#666;font-weight:normal">(含分润)</span>:</b><span class="right pnl-${pnlCls}">${n.pnl >= 0 ? "+" : ""}${fmt(n.pnl)} U</span>
    </div>`;
  }
  html += `<div class="actions">
    <button data-action="download-csv" title="下载该地址 + 全部上线 + 全部下线的明细 CSV">⬇ CSV</button>
    <button class="secondary" data-action="download-xlsx" title="Excel 文件 (.xlsx,带格式 + 多 sheet)">⬇ Excel</button>
    <button class="secondary" data-action="download-docx" title="Word 文档(Word / Pages / Google Docs 可直接打开)">⬇ Word</button>
  </div>`;
  html += `</div>`;

  // —— 伞下汇总(焦点 + 下线整树)——
  const agg = umbrellaAggregate(addr, desc);
  const aggPnlCls = pnlClass(agg.realized_pnl);
  html += `<details open class="umbrella">
    <summary>伞下汇总 <span class="count">${agg.count_total} 人 (含本人)</span></summary>
    <div class="stat" style="margin-top:6px">
      <b>伞下总人数:</b><span class="right">${agg.count_total}</span>
      <b>&nbsp;&nbsp;盈利 / 亏损 / 被盗:</b><span class="right">
        <span class="pnl-pos">${agg.count_profit}</span> /
        <span class="pnl-neg">${agg.count_loss}</span> /
        <span class="pnl-zero">${agg.count_stolen}</span>
      </span>
      <b>累计 stake (伞下):</b><span class="right">${fmt(agg.stake)} U</span>
      <b>累计 unstake (伞下):</b><span class="right">${fmt(agg.unstake)} U</span>
      <b>钱包总收款 (伞下):</b><span class="right">${fmt(agg.wallet_in_total)} U</span>
      <b>&nbsp;&nbsp;其中分润奖励 (伞下):</b><span class="right">${fmt(agg.commission_etc)} U</span>
      <b>在押本金 (伞下):</b><span class="right">${fmt(agg.in_stake)} U</span>
      <b>疑似被盗本金 (伞下):</b><span class="right">${fmt(agg.stolen)} U</span>
      <b>应赔付总额 (伞下):</b><span class="right">${fmt(agg.owed)} U</span>
      <b>已实现盈亏 <span style="color:#666;font-weight:normal">(含分润, 伞下)</span>:</b><span class="right pnl-${aggPnlCls}">${agg.realized_pnl >= 0 ? "+" : ""}${fmt(agg.realized_pnl)} U</span>
    </div>
  </details>`;

  // —— 上线链 ——
  html += `<details open>
    <summary>上线链 <span class="count">${ancestors.length} 层 → Genesis</span></summary>
    <ul class="chain">`;
  if (ancestors.length === 0) {
    html += `<li class="empty">无上级(已是 Genesis)</li>`;
  } else {
    for (let i = 0; i < ancestors.length; i++) {
      const p = ancestors[i];
      const pn = nodeById.get(p);
      const ppnlCls = pnlClass(pn.pnl);
      const pnl = pn.is_participant ? `<span class="pnl ${ppnlCls}">${pn.pnl >= 0 ? "+" : ""}${fmt(pn.pnl)}</span>` : "";
      const al = getAlias(p);
      const aliasSpan = al ? `<span class="alias" style="color:${aliasColor(p)}">${escapeHtml(al)}</span>` : (pn.is_genesis ? `<span class="alias">GENESIS</span>` : "");
      html += `<li class="node-row" data-addr="${p}">
        <span class="depth">↑${i + 1}</span>
        ${aliasSpan}<span class="addr">${p}</span>
        ${pnl}
      </li>`;
    }
  }
  html += `</ul></details>`;

  // —— 下线树 ——
  const totalDown = desc.total;
  html += `<details open>
    <summary>下线树 <span class="count">${totalDown} 人 · ${desc.layers.length} 层</span></summary>`;
  if (totalDown === 0) {
    html += `<div class="empty">无直推</div>`;
  } else {
    html += `<div class="hint">⊕ 点开展开 · 点击地址定位</div>`;
    html += renderDownTree(addr, 1, 30);
  }
  html += `</details>`;

  panelBody.innerHTML = html;

  // 绑定点击
  panelBody.querySelectorAll("li.node-row").forEach((el) => {
    el.addEventListener("click", (e) => {
      const tgt = e.target;
      if (tgt && tgt.classList && tgt.classList.contains("toggle")) return;
      const a = el.getAttribute("data-addr");
      if (a) selectNode(a);
    });
  });
  // 绑定 ⊕/⊖
  panelBody.querySelectorAll(".toggle").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      const li = el.closest("li");
      const childUl = li.querySelector("ul.tree");
      if (!childUl) return;
      const isOpen = childUl.style.display !== "none";
      childUl.style.display = isOpen ? "none" : "";
      el.textContent = isOpen ? "⊕" : "⊖";
    });
  });
  // 标记 current
  panelBody.querySelectorAll(`li.node-row[data-addr="${addr}"]`).forEach((el) => el.classList.add("is-current"));
  // 绑定下载按钮
  panelBody.querySelectorAll("button[data-action]").forEach((btn) => {
    btn.onclick = () => {
      const action = btn.getAttribute("data-action");
      if (action === "download-csv") downloadDetails(addr, "csv");
      else if (action === "download-xlsx") downloadDetails(addr, "xlsx");
      else if (action === "download-docx") downloadDetails(addr, "docx");
    };
  });
  // —— alias 编辑/添加按钮 ——
  panelBody.querySelectorAll("button[data-alias-action]").forEach((btn) => {
    btn.onclick = () => {
      const action = btn.getAttribute("data-alias-action");
      if (action === "remove") {
        if (confirm("移除别名「" + getAlias(addr) + "」?")) {
          setAlias(addr, "");
          selectNode(addr);   // 重渲染
          draw();
        }
        return;
      }
      // add / edit → 切换为 input
      const host = document.getElementById("alias-row-host");
      const cur = getAlias(addr) || "";
      host.innerHTML = `<input type="text" id="alias-edit-input" value="${escapeHtml(cur)}" placeholder="如 紫悦 / 牛逼哥" autofocus />
        <button data-alias-action="save" style="color:#22c55e">✓ 保存</button>
        <button data-alias-action="cancel">取消</button>`;
      const input = document.getElementById("alias-edit-input");
      input.focus(); input.select();
      const finish = (commit) => {
        if (commit) setAlias(addr, input.value);
        selectNode(addr);  // 重渲染
        draw();
      };
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") finish(true);
        else if (e.key === "Escape") finish(false);
      });
      host.querySelector('[data-alias-action="save"]').onclick = () => finish(true);
      host.querySelector('[data-alias-action="cancel"]').onclick = () => finish(false);
    };
  });
}

// —— 下载明细: 焦点 + 全部上线 + 全部下线 ——
function buildDetailRows(addr) {
  const rows = [];
  const focus = nodeById.get(addr);
  const addRow = (kind, layer, n) => rows.push({
    kind, layer, address: n.id,
    depth_in_tree: n.depth,
    is_genesis: n.is_genesis,
    is_participant: n.is_participant,
    is_top50: n.is_top50,
    stake: n.stake, unstake: n.unstake,
    in_stake: n.in_stake, stolen: n.stolen, owed: n.owed,
    realized_pnl: n.pnl, pnl_after_comp: n.pnl_after_comp,
    wallet_in_total: n.wallet_in_total,
    wallet_in_count: n.wallet_in_count,
    commission_etc: n.commission_etc,
    net_cashflow: n.net_cashflow,
  });
  addRow("focus", 0, focus);
  const ancestors = ancestorsOf(addr);
  ancestors.forEach((a, i) => addRow("upline", i + 1, nodeById.get(a)));
  // 下线 BFS 扁平化(按层)
  const desc = descendantsOf(addr, 100);
  desc.layers.forEach((layer, i) => {
    for (const a of layer) addRow("downline", i + 1, nodeById.get(a));
  });
  return rows;
}

function downloadDetails(addr, fmt) {
  const rows = buildDetailRows(addr);
  const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const shortF = addr.slice(0, 6) + "_" + addr.slice(-4);
  let blob, filename;
  if (fmt === "xlsx") {
    // —— Excel 文件: 用 SheetJS,3 个 sheet (摘要 + 上线链 + 下线树) ——
    if (typeof XLSX === "undefined") {
      alert("Excel 库未加载,请检查网络后刷新页面再试");
      return;
    }
    const focusNode = nodeById.get(addr);
    const focusAlias = getAlias(addr);
    const desc = descendantsOf(addr, 100);
    const agg = umbrellaAggregate(addr, desc);
    const upRows = rows.filter(r => r.kind === "upline");
    const downRows = rows.filter(r => r.kind === "downline");
    const num = (v) => typeof v === "number" ? Number(v.toFixed(4)) : v;
    const kindZh = { focus: "焦点", upline: "上线", downline: "下线" };
    const layerStr = (r) => r.kind === "focus" ? 0 : (r.kind === "upline" ? "↑" + r.layer : "↓" + r.layer);

    // Sheet 1: 摘要(焦点 + 伞下汇总)
    const summary = [
      ["字段", "焦点本人", "伞下汇总(含焦点)"],
      ["别名", focusAlias || "", ""],
      ["地址", addr, ""],
      ["身份", focusNode.is_genesis ? "GENESIS" : (focusNode.is_participant ? (focusNode.is_top50 ? "Top 50 大户" : "参与者") : "祖先"), ""],
      ["在 referrer 树深度", focusNode.depth, ""],
      ["上线链层数", upRows.length, ""],
      ["伞下总人数(含本人)", "", agg.count_total],
      ["盈利人数(伞下)", "", agg.count_profit],
      ["亏损人数(伞下)", "", agg.count_loss],
      ["疑似被盗人数(伞下)", "", agg.count_stolen],
      ["累计 stake (U)", num(focusNode.stake), num(agg.stake)],
      ["累计 unstake (U)", num(focusNode.unstake), num(agg.unstake)],
      ["钱包总收款 (U)", num(focusNode.wallet_in_total), num(agg.wallet_in_total)],
      ["其中分润奖励 (U)", num(focusNode.commission_etc), num(agg.commission_etc)],
      ["钱包收款笔数", focusNode.wallet_in_count, ""],
      ["在押本金 (U)", num(focusNode.in_stake), num(agg.in_stake)],
      ["疑似被盗本金 (U)", num(focusNode.stolen), num(agg.stolen)],
      ["应赔付总额 (U)", num(focusNode.owed), num(agg.owed)],
      ["已实现盈亏 含分润 (U)", num(focusNode.pnl), num(agg.realized_pnl)],
    ];

    const headers = [
      "类型", "层级", "地址", "别名", "深度", "是否Top50",
      "累计stake(U)", "累计unstake(U)",
      "钱包总收款(U)", "钱包收款笔数", "分润奖励(U)",
      "在押本金(U)", "疑似被盗本金(U)", "应赔付总额(U)",
      "已实现盈亏(含分润)(U)"
    ];
    const rowToArr = (r) => [
      kindZh[r.kind] || r.kind,
      layerStr(r),
      r.address,
      getAlias(r.address) || "",
      r.depth_in_tree,
      r.is_top50 ? "是" : "",
      num(r.stake), num(r.unstake),
      num(r.wallet_in_total), r.wallet_in_count, num(r.commission_etc),
      num(r.in_stake), num(r.stolen), num(r.owed),
      num(r.realized_pnl),
    ];

    // Sheet 2: 上线链
    const upRowsXlsx = [headers, ...rows.filter(r => r.kind === "focus" || r.kind === "upline").map(rowToArr)];
    // Sheet 3: 下线树
    const downRowsXlsx = [headers, ...rows.filter(r => r.kind === "focus" || r.kind === "downline").map(rowToArr)];

    const wb = XLSX.utils.book_new();
    const ws1 = XLSX.utils.aoa_to_sheet(summary);
    ws1["!cols"] = [{ wch: 22 }, { wch: 48 }, { wch: 22 }];
    XLSX.utils.book_append_sheet(wb, ws1, "摘要");
    const ws2 = XLSX.utils.aoa_to_sheet(upRowsXlsx);
    ws2["!cols"] = [{wch:6},{wch:6},{wch:46},{wch:14},{wch:6},{wch:7},{wch:13},{wch:13},{wch:13},{wch:10},{wch:13},{wch:12},{wch:14},{wch:13},{wch:18}];
    XLSX.utils.book_append_sheet(wb, ws2, "上线链");
    const ws3 = XLSX.utils.aoa_to_sheet(downRowsXlsx);
    ws3["!cols"] = ws2["!cols"];
    XLSX.utils.book_append_sheet(wb, ws3, "下线树");

    const xlsxBuf = XLSX.write(wb, { bookType: "xlsx", type: "array" });
    blob = new Blob([xlsxBuf], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
    filename = `referrer_${shortF}_${ts}.xlsx`;
  } else {
    const headers = [
      "类型", "层级", "地址", "别名", "在referrer树深度", "是否Genesis", "是否参与者", "是否Top50",
      "累计stake(U)", "累计unstake(U)",
      "钱包总收款(U)", "钱包收款笔数", "分润奖励(U)",
      "在押本金(U)", "疑似被盗本金(U)", "应赔付总额(U)",
      "已实现盈亏(含分润)(U)"
    ];
    const escape = (v) => {
      if (v == null) return "";
      const s = String(v);
      return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    const fmtNum = (v) => (typeof v === "number" ? v.toFixed(4) : v);
    const kindZh = { focus: "焦点", upline: "上线", downline: "下线" };
    const layerStr = (r) => r.kind === "focus" ? "0" : (r.kind === "upline" ? "↑" + r.layer : "↓" + r.layer);
    const lines = [headers.join(",")];
    for (const r of rows) {
      lines.push([
        kindZh[r.kind] || r.kind,
        layerStr(r),
        r.address,
        getAlias(r.address) || "",
        r.depth_in_tree,
        r.is_genesis ? "是" : "",
        r.is_participant ? "是" : "",
        r.is_top50 ? "是" : "",
        fmtNum(r.stake), fmtNum(r.unstake),
        fmtNum(r.wallet_in_total), r.wallet_in_count, fmtNum(r.commission_etc),
        fmtNum(r.in_stake), fmtNum(r.stolen), fmtNum(r.owed),
        fmtNum(r.realized_pnl),
      ].map(escape).join(","));
    }
    // UTF-8 BOM 让 Excel 正确识别中文
    blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
    filename = `referrer_${shortF}_${ts}.csv`;
  }
  if (fmt === "docx") {
    // Word HTML 格式: Word / Pages / Google Docs 都能打开
    const focusNode = nodeById.get(addr);
    const focusAlias = getAlias(addr);
    const headers = [
      "类型", "层级", "地址", "别名",
      "累计 stake (U)", "累计 unstake (U)",
      "钱包总收款 (U)", "分润奖励 (U)",
      "在押本金 (U)", "应赔付 (U)",
      "已实现盈亏 含分润 (U)"
    ];
    const kindZh = { focus: "焦点", upline: "上线", downline: "下线" };
    const layerStr = (r) => r.kind === "focus" ? "—" : (r.kind === "upline" ? "↑" + r.layer : "↓" + r.layer);
    const fmtN = (v) => (typeof v === "number" ? v.toLocaleString("en-US", {maximumFractionDigits: 2}) : v);
    const pnlColor = (v) => v > 0.01 ? "#16a34a" : (v < -0.01 ? "#dc2626" : "#525252");

    const rowHtml = (r) => {
      const tone = r.kind === "focus" ? "background:#fef3c7;font-weight:600"
        : r.kind === "upline" ? "background:#fef9c3" : "";
      return `<tr style="${tone}">
        <td>${kindZh[r.kind] || r.kind}</td>
        <td>${layerStr(r)}</td>
        <td style="font-family:Consolas,monospace;font-size:10pt">${r.address}</td>
        <td style="color:#ca8a04">${escapeHtml(getAlias(r.address) || "")}</td>
        <td style="text-align:right">${fmtN(r.stake)}</td>
        <td style="text-align:right">${fmtN(r.unstake)}</td>
        <td style="text-align:right">${fmtN(r.wallet_in_total)}</td>
        <td style="text-align:right">${fmtN(r.commission_etc)}</td>
        <td style="text-align:right">${fmtN(r.in_stake)}</td>
        <td style="text-align:right">${fmtN(r.owed)}</td>
        <td style="text-align:right;color:${pnlColor(r.realized_pnl)}">${r.realized_pnl >= 0 ? "+" : ""}${fmtN(r.realized_pnl)}</td>
      </tr>`;
    };

    const upRows = rows.filter(r => r.kind === "upline");
    const downRows = rows.filter(r => r.kind === "downline");
    const focusRow = rows.find(r => r.kind === "focus");

    // 计算伞下汇总
    const agg = umbrellaAggregate(addr, descendantsOf(addr, 100));

    const html = `<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word" xmlns="http://www.w3.org/TR/REC-html40">
<head><meta charset="utf-8"><title>必赢 referrer 明细</title>
<style>
  body { font-family: "Microsoft YaHei","PingFang SC","Arial",sans-serif; font-size:11pt; color:#171717; }
  h1 { font-size:18pt; margin:0 0 6pt 0; }
  h2 { font-size:14pt; margin:18pt 0 6pt 0; color:#0f172a; border-bottom:1px solid #cbd5e1; padding-bottom:3pt; }
  .meta { color:#525252; font-size:10pt; margin-bottom:18pt; }
  .stat-grid { width:100%; border-collapse:collapse; margin:8pt 0; }
  .stat-grid td { padding:4pt 8pt; border:1px solid #e5e5e5; vertical-align:top; }
  .stat-grid td:first-child { background:#f5f5f4; font-weight:500; width:30%; }
  table.detail { width:100%; border-collapse:collapse; margin-top:8pt; font-size:10pt; }
  table.detail th { background:#1e293b; color:#fff; padding:5pt 6pt; text-align:left; font-weight:500; }
  table.detail td { padding:4pt 6pt; border:0.5pt solid #e5e5e5; }
</style>
</head>
<body>
<h1>必赢 referrer 明细 — ${escapeHtml(focusAlias || addr)}</h1>
<div class="meta">
  地址 <span style="font-family:Consolas,monospace">${addr}</span> · 深度 ${focusNode.depth} · 生成 ${ts.replace(/-/g, ":").replace(/T/, " ")} ·
  本人 ${upRows.length} 层上线 · ${downRows.length} 个下线
</div>

<h2>① 焦点本人统计</h2>
<table class="stat-grid">
  <tr><td>累计 stake</td><td>${fmtN(focusRow.stake)} U</td></tr>
  <tr><td>累计 unstake (合约口径)</td><td>${fmtN(focusRow.unstake)} U</td></tr>
  <tr><td>钱包总收款 (含分润)</td><td>${fmtN(focusRow.wallet_in_total)} U &nbsp;(${focusRow.wallet_in_count} 笔)</td></tr>
  <tr><td>&nbsp;&nbsp;&nbsp;其中分润奖励</td><td>${fmtN(focusRow.commission_etc)} U</td></tr>
  <tr><td>在押本金</td><td>${fmtN(focusRow.in_stake)} U</td></tr>
  <tr><td>疑似被盗本金</td><td>${fmtN(focusRow.stolen)} U</td></tr>
  <tr><td>应赔付总额</td><td>${fmtN(focusRow.owed)} U</td></tr>
  <tr><td><b>已实现盈亏 (含分润)</b></td><td style="color:${pnlColor(focusRow.realized_pnl)};font-weight:600">${focusRow.realized_pnl >= 0 ? "+" : ""}${fmtN(focusRow.realized_pnl)} U</td></tr>
</table>

<h2>② 伞下汇总 (焦点 + ${agg.count_total - 1} 个下线)</h2>
<table class="stat-grid">
  <tr><td>伞下总人数</td><td>${agg.count_total}</td></tr>
  <tr><td>盈利 / 亏损 / 被盗</td><td><span style="color:#16a34a">${agg.count_profit}</span> / <span style="color:#dc2626">${agg.count_loss}</span> / <span style="color:#525252">${agg.count_stolen}</span></td></tr>
  <tr><td>累计 stake (伞下)</td><td>${fmtN(agg.stake)} U</td></tr>
  <tr><td>累计 unstake (伞下)</td><td>${fmtN(agg.unstake)} U</td></tr>
  <tr><td>钱包总收款 (伞下)</td><td>${fmtN(agg.wallet_in_total)} U</td></tr>
  <tr><td>&nbsp;&nbsp;&nbsp;其中分润奖励 (伞下)</td><td>${fmtN(agg.commission_etc)} U</td></tr>
  <tr><td>在押本金 (伞下)</td><td>${fmtN(agg.in_stake)} U</td></tr>
  <tr><td>应赔付总额 (伞下)</td><td>${fmtN(agg.owed)} U</td></tr>
  <tr><td><b>已实现盈亏 (含分润, 伞下)</b></td><td style="color:${pnlColor(agg.realized_pnl)};font-weight:600">${agg.realized_pnl >= 0 ? "+" : ""}${fmtN(agg.realized_pnl)} U</td></tr>
</table>

<h2>③ 上线链 (${upRows.length} 层 → Genesis)</h2>
<table class="detail">
  <tr><th>${headers.join("</th><th>")}</th></tr>
  ${upRows.map(rowHtml).join("")}
</table>

<h2>④ 下线树 (${downRows.length} 人, 按层级排序)</h2>
<table class="detail">
  <tr><th>${headers.join("</th><th>")}</th></tr>
  ${downRows.map(rowHtml).join("")}
</table>

</body></html>`;
    blob = new Blob(["﻿" + html], { type: "application/msword;charset=utf-8" });
    filename = `referrer_${shortF}_${ts}.doc`;
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

function renderDownTree(parentAddr, depth, maxDepth) {
  if (depth > maxDepth) return "";
  const kids = childrenMap.get(parentAddr) || [];
  if (!kids.length) return "";
  // 按 stake 倒序
  kids.sort((a, b) => {
    const na = nodeById.get(a), nb = nodeById.get(b);
    return (nb.stake || 0) - (na.stake || 0);
  });
  let html = `<ul class="tree">`;
  for (const c of kids) {
    const cn = nodeById.get(c);
    const subKids = childrenMap.get(c) || [];
    const hasKids = subKids.length > 0 && depth < maxDepth;
    const pcls = pnlClass(cn.pnl);
    const pnl = cn.is_participant ? `<span class="pnl ${pcls}">${cn.pnl >= 0 ? "+" : ""}${fmt(cn.pnl)}</span>` : "";
    const al = getAlias(c);
    const aliasSpan = al ? `<span class="alias" style="color:${aliasColor(c)}">${escapeHtml(al)}</span>` : "";
    html += `<li class="node-row" data-addr="${c}">
      ${hasKids
        ? `<span class="toggle" title="展开 ${subKids.length} 个">⊕</span>`
        : `<span class="leaf-spacer"></span>`}
      <span class="depth">↓${depth}</span>
      ${aliasSpan}<span class="addr">${c}</span>
      ${pnl}`;
    // 缺省折叠;但写出 children 树 (display:none),由 ⊕ 切换
    if (hasKids) {
      html += `<div style="display:none">${renderDownTree(c, depth + 1, maxDepth)}</div>`;
    }
    html += `</li>`;
  }
  html += `</ul>`;
  return html;
}

// 注意: 上面 renderDownTree 把子树放在 <div style="display:none"> 里,然后 ⊕ 切换时
// 我去找 li 下的 ul.tree。但子 ul 是在 div 里。改一下选择器:
function bindToggles() {
  panelBody.querySelectorAll(".toggle").forEach((el) => {
    el.onclick = (e) => {
      e.stopPropagation();
      const li = el.closest("li");
      // li 直接子 div 是 children 容器
      const childDiv = Array.from(li.children).find(c => c.tagName === "DIV");
      if (!childDiv) return;
      const isOpen = childDiv.style.display !== "none";
      childDiv.style.display = isOpen ? "none" : "";
      el.textContent = isOpen ? "⊕" : "⊖";
    };
  });
}

// —— 绘制 ——
function draw() {
  ctx.save();
  ctx.fillStyle = "#0a0a0a";
  ctx.fillRect(0, 0, W, H);
  ctx.translate(transform.x, transform.y);
  ctx.scale(transform.k, transform.k);

  // 边 — link 永远是 child→parent (src=child, dst=parent),所以判断方向时:
  //   边在「焦点向上的祖先链」上 → 上线侧 → 紫色
  //   边在「焦点向下的子树」内   → 下线侧 → 金色
  // 这两个集合 highlightUp/highlightDown 不交叉(分别在焦点之上/之下)
  for (const l of links) {
    const s = l.source, t = l.target;
    if (!s || !t || s.x === undefined) continue;
    let edgeMode = "normal";   // "normal" | "up" | "down"
    if (selected) {
      const sIsSelf = s.id === selected;
      const tIsSelf = t.id === selected;
      const sIsUp   = highlightUp.has(s.id);
      const tIsUp   = highlightUp.has(t.id);
      const sIsDown = highlightDown.has(s.id);
      const tIsDown = highlightDown.has(t.id);
      // 上线链上的边: child 是焦点/上线,parent 是上线 (dst=parent 是 up 或 dst=self & src 是 up 不可能因为 src=child)
      // 边是 child→parent: 焦点本人的 parent 边 = (src=self, dst=upline) → 上线
      // 上线链中间的边 = (src=upline, dst=upline) → 上线
      // 下线树中的边 = (src=downline, dst=焦点 or downline) → 下线
      if ((sIsSelf && tIsUp) || (sIsUp && tIsUp)) {
        edgeMode = "up";
      } else if ((sIsDown && tIsSelf) || (sIsDown && tIsDown)) {
        edgeMode = "down";
      }
    }
    if (edgeMode === "up") {
      ctx.strokeStyle = "rgba(168,85,247,0.95)";    // 紫色 #a855f7
      ctx.lineWidth = 1.8 / transform.k;
    } else if (edgeMode === "down") {
      ctx.strokeStyle = "rgba(251,191,36,0.95)";    // 金色 #fbbf24
      ctx.lineWidth = 1.6 / transform.k;
    } else {
      ctx.strokeStyle = edgeColor(l.child);
      ctx.lineWidth = 0.5 / transform.k;
    }
    ctx.beginPath();
    ctx.moveTo(s.x, s.y);
    ctx.lineTo(t.x, t.y);
    ctx.stroke();
  }

  // 节点
  for (const n of data.nodes) {
    if (n.x === undefined) continue;
    const r = nodeRadius(n);
    const isSelf = selected === n.id;
    const isUp = highlightUp.has(n.id);
    const isDown = highlightDown.has(n.id);
    const dimmed = selected && !isSelf && !isUp && !isDown && !n.is_genesis;
    ctx.beginPath();
    ctx.arc(n.x, n.y, isSelf ? r * 1.4 : r, 0, Math.PI * 2);
    ctx.globalAlpha = dimmed ? 0.18 : 1;
    ctx.fillStyle = nodeColor(n);
    ctx.fill();
    ctx.globalAlpha = 1;
    if (n.is_genesis) {
      ctx.lineWidth = 2 / transform.k;
      ctx.strokeStyle = "#fde047";
      ctx.stroke();
    }
    if (isSelf) {
      ctx.lineWidth = 3.5 / transform.k;
      ctx.strokeStyle = "#fafafa";
      ctx.stroke();
    } else if (isUp) {
      ctx.lineWidth = 1.8 / transform.k;
      ctx.strokeStyle = "rgba(168,85,247,0.9)";  // 紫色描边 = 上线
      ctx.stroke();
    } else if (isDown) {
      ctx.lineWidth = 1.4 / transform.k;
      ctx.strokeStyle = "rgba(251,191,36,0.85)";  // 金色描边 = 下线
      ctx.stroke();
    }
  }

  // 标签: Top 50 + Genesis + selected + 任何有 alias 的地址
  const fontSize = 10 / transform.k;
  ctx.font = fontSize + "px -apple-system, sans-serif";
  ctx.textAlign = "left";
  labelBoxes.length = 0;  // 重置标签点击命中框(给 click 用)
  for (const n of data.nodes) {
    const al = getAlias(n.id);
    if (!n.is_top50 && !n.is_genesis && selected !== n.id && !al) continue;
    if (n.x === undefined) continue;
    const r = nodeRadius(n);
    let label;
    if (al) {
      // 有别名 → 优先显示别名;私加用 🔒,内置用 📒
      label = (isUserAlias(n.id) ? "🔒 " : "📒 ") + al;
    } else if (n.is_genesis) {
      label = "GENESIS";
    } else if (selected === n.id) {
      label = n.id;
    } else {
      label = shortAddr(n.id);
    }
    ctx.fillStyle = al
      ? aliasColor(n.id)
      : (n.is_genesis ? "#fde047" : (selected === n.id ? "#fafafa" : "rgba(250,250,250,0.85)"));
    const tx = n.x + r + 2;
    const ty = n.y + 3;
    ctx.fillText(label, tx, ty);
    // 记下世界坐标的标签边界框 (用于 click 命中)
    const w = ctx.measureText(label).width;
    labelBoxes.push({ id: n.id, x1: tx, y1: ty - fontSize, x2: tx + w, y2: ty + 2 });
  }

  ctx.restore();
}

simulation.on("tick", draw);

// —— 点击节点 ——
canvas.addEventListener("click", (e) => {
  const rect = canvas.getBoundingClientRect();
  const wx = (e.clientX - rect.left - transform.x) / transform.k;
  const wy = (e.clientY - rect.top - transform.y) / transform.k;
  // 1) 圆形命中(优先)
  for (const n of data.nodes) {
    if (n.x === undefined) continue;
    const r = nodeRadius(n) + 1;
    const dx = n.x - wx, dy = n.y - wy;
    if (dx * dx + dy * dy <= r * r) {
      selectNode(n.id);
      return;
    }
  }
  // 2) 文字标签命中(团队长名字、Top50、Genesis 等)
  for (const b of labelBoxes) {
    if (wx >= b.x1 && wx <= b.x2 && wy >= b.y1 && wy <= b.y2) {
      selectNode(b.id);
      return;
    }
  }
});

// —— hover tooltip ——
canvas.addEventListener("mousemove", (e) => {
  const rect = canvas.getBoundingClientRect();
  const wx = (e.clientX - rect.left - transform.x) / transform.k;
  const wy = (e.clientY - rect.top - transform.y) / transform.k;
  let found = null;
  // 圆形命中
  for (const n of data.nodes) {
    if (n.x === undefined) continue;
    const r = nodeRadius(n) + 1;
    const dx = n.x - wx, dy = n.y - wy;
    if (dx * dx + dy * dy <= r * r) { found = n; break; }
  }
  // 文字标签命中(团队长名字)
  if (!found) {
    for (const b of labelBoxes) {
      if (wx >= b.x1 && wx <= b.x2 && wy >= b.y1 && wy <= b.y2) {
        found = nodeById.get(b.id);
        break;
      }
    }
  }
  if (found) {
    canvas.style.cursor = "pointer";
    const pnlCls = pnlClass(found.pnl);
    const role = found.is_genesis ? "GENESIS" : (found.is_participant ? (found.is_top50 ? "Top 50" : "参与者") : "祖先");
    const al = getAlias(found.id);
    const aliasLine = al ? `<div style="color:${aliasColor(found.id)};font-weight:600;margin-bottom:4px;font-family:-apple-system,sans-serif">${isUserAlias(found.id) ? "🔒" : "📒"} ${escapeHtml(al)}</div>` : "";
    tooltip.innerHTML = `${aliasLine}<div class="addr">${found.id}</div>
      <div class="row"><b>${role}</b> · 深度 ${found.depth}</div>
      ${found.is_participant ? `
      <div class="row">stake ${fmt(found.stake)} · 钱包总收款 ${fmt(found.wallet_in_total)}</div>
      <div class="row">已实现盈亏(含分润) <span class="pnl-${pnlCls}">${found.pnl >= 0 ? "+" : ""}${fmt(found.pnl)} U</span></div>
      <div class="row" style="color:#666;font-size:11px">点击查看上下线 / 改别名</div>` : ""}`;
    tooltip.style.display = "block";
    tooltip.style.left = (Math.min(e.clientX + 14, W - 360)) + "px";
    tooltip.style.top = (e.clientY + 14) + "px";
  } else {
    canvas.style.cursor = "grab";
    tooltip.style.display = "none";
  }
});

canvas.addEventListener("mouseleave", () => tooltip.style.display = "none");

// —— 搜索: input + Enter,支持地址前缀 + alias 名字 ——
function doSearch() {
  const raw = searchInput.value.trim().toLowerCase();
  if (!raw) { clearSelection(); return; }
  let hit = null;
  // 1) 地址前缀(精确 → 前缀)
  if (raw.startsWith("0x") || /^[0-9a-f]{4,}$/.test(raw)) {
    const q = raw.startsWith("0x") ? raw : "0x" + raw;
    hit = nodeById.get(q) || data.nodes.find(n => n.id.startsWith(q));
  }
  // 2) alias 名字(精确 → 包含)
  if (!hit) {
    const exactKey = Object.keys(aliases).find(k => aliases[k].toLowerCase() === raw);
    if (exactKey) hit = nodeById.get(exactKey);
  }
  if (!hit) {
    const partialKey = Object.keys(aliases).find(k => aliases[k].toLowerCase().includes(raw));
    if (partialKey) hit = nodeById.get(partialKey);
  }
  if (hit) {
    searchInput.classList.remove("no-hit");
    selectNode(hit.id);
  } else {
    searchInput.classList.add("no-hit");
  }
}
searchInput.addEventListener("input", doSearch);
searchInput.addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });
searchInput.addEventListener("paste", () => setTimeout(doSearch, 0));

// —— resize ——
window.addEventListener("resize", () => {
  W = window.innerWidth; H = window.innerHeight;
  sizeCanvas();
  draw();
});

// —— 状态条 ——
let ticks = 0;
simulation.on("tick.status", () => {
  ticks++;
  if (ticks % 5 === 0) {
    const a = simulation.alpha();
    statusEl.textContent = a < 0.01
      ? "已稳定 · " + ticks + " ticks · 拖拽节点继续扰动 · 点击节点查看详情"
      : "仿真收敛中 α=" + a.toFixed(4) + " · " + ticks + " ticks";
  }
});

// Esc 关闭面板 / alias modal
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    const modal = document.getElementById("alias-modal");
    if (modal.classList.contains("open")) modal.classList.remove("open");
    else clearSelection();
  }
});

// —— alias manager modal ——
const aliasModal = document.getElementById("alias-modal");
const aliasList = document.getElementById("alias-list");

function renderAliasList() {
  const entries = Object.entries(aliases).sort((a, b) => {
    // 用户私加优先靠上
    const ua = isUserAlias(a[0]) ? 0 : 1;
    const ub = isUserAlias(b[0]) ? 0 : 1;
    if (ua !== ub) return ua - ub;
    return a[1].localeCompare(b[1]);
  });
  if (!entries.length) {
    aliasList.innerHTML = `<div class="empty-tip">还没有任何别名,在下面添加第一个或在面板里编辑。</div>`;
    return;
  }
  aliasList.innerHTML = `<div style="color:#888;font-size:11px;padding:4px 0 8px">🔒 = 你私加的(只你可见) · 📒 = 内置默认(所有人可见)</div>`
    + entries.map(([addr, name]) => {
      const u = isUserAlias(addr);
      const tag = `<span style="color:${u ? "#22d3ee" : "#fde047"};font-size:14px;width:18px;display:inline-block" title="${u ? "私加(只你可见)" : "内置(所有人可见)"}">${u ? "🔒" : "📒"}</span>`;
      return `<div class="row" data-addr="${addr}">
        ${tag}
        <input class="name" value="${escapeHtml(name)}" data-role="name" style="color:${u ? "#22d3ee" : "#fde047"}" />
        <input class="addr" value="${addr}" readonly />
        <button data-action="goto" title="定位">→</button>
        <button data-action="delete" title="${u ? "删除私加" : "覆盖内置 (改回时点 + 起个名字)"}">✕</button>
      </div>`;
    }).join("");
  aliasList.querySelectorAll(".row").forEach((row) => {
    const addr = row.getAttribute("data-addr");
    row.querySelector('[data-role="name"]').addEventListener("change", (e) => {
      setAlias(addr, e.target.value);
      if (selected) selectNode(selected);
      draw();
    });
    row.querySelector('[data-action="delete"]').onclick = () => {
      setAlias(addr, "");
      renderAliasList();
      if (selected) selectNode(selected);
      draw();
    };
    row.querySelector('[data-action="goto"]').onclick = () => {
      aliasModal.classList.remove("open");
      if (nodeById.has(addr)) selectNode(addr);
      else alert("该地址不在当前图中(不在参与者范围内)");
    };
  });
}

document.getElementById("alias-btn").onclick = () => {
  renderAliasList();
  aliasModal.classList.add("open");
};
document.getElementById("alias-close").onclick = () => aliasModal.classList.remove("open");
aliasModal.addEventListener("click", (e) => {
  if (e.target === aliasModal) aliasModal.classList.remove("open");
});

document.getElementById("alias-new-add").onclick = () => {
  const addrEl = document.getElementById("alias-new-addr");
  const nameEl = document.getElementById("alias-new-name");
  const addr = addrEl.value.trim().toLowerCase();
  const name = nameEl.value.trim();
  if (!/^0x[0-9a-f]{40}$/.test(addr)) { alert("地址格式不对,要 0x + 40 个 hex 字符"); return; }
  if (!name) { alert("别名不能为空"); return; }
  setAlias(addr, name);
  addrEl.value = ""; nameEl.value = "";
  renderAliasList();
  if (selected) selectNode(selected);
  draw();
};

document.getElementById("alias-export").onclick = () => {
  const blob = new Blob([JSON.stringify(aliases, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "biying_aliases.json";
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
};

document.getElementById("alias-import").onclick = () => {
  const input = document.createElement("input");
  input.type = "file"; input.accept = ".json,application/json";
  input.onchange = () => {
    const file = input.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const parsed = JSON.parse(reader.result);
        if (typeof parsed !== "object" || !parsed) throw new Error("不是 JSON 对象");
        let n = 0;
        for (const [k, v] of Object.entries(parsed)) {
          if (/^0x[0-9a-f]{40}$/.test(k.toLowerCase()) && typeof v === "string") {
            aliases[k.toLowerCase()] = v;
            n++;
          }
        }
        saveAliases();
        renderAliasList();
        if (selected) selectNode(selected);
        draw();
        alert(`导入 ${n} 条别名(已合并到现有)`);
      } catch (e) {
        alert("导入失败: " + e.message);
      }
    };
    reader.readAsText(file);
  };
  input.click();
};

document.getElementById("alias-clear").onclick = () => {
  if (!Object.keys(aliases).length) { alert("已经是空的了"); return; }
  if (confirm(`确定清空所有 ${Object.keys(aliases).length} 条别名? 此操作不可撤销。`)) {
    aliases = {};
    saveAliases();
    renderAliasList();
    if (selected) selectNode(selected);
    draw();
  }
};

console.log("BubbleMap ready · nodes=" + data.nodes.length + " · edges=" + links.length + " · aliases=" + Object.keys(aliases).length);
</script>
</body>
</html>
"""


def main() -> None:
    if not PARENT_MAP.exists():
        raise SystemExit(f"先跑 build_parent_map.py 生成 {PARENT_MAP}")

    print("加载数据…")
    parent_map = json.loads(PARENT_MAP.read_text())
    pnl = load_pnl()
    print(f"  parent_map: {len(parent_map)} 条")
    print(f"  盈亏表参与者: {len(pnl)} 个")

    graph = build_graph(parent_map, pnl)
    graph["generated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(
        f"  图节点: {graph['stats']['total_nodes']} (参与者 {graph['stats']['participants']} "
        f"+ 祖先 {graph['stats']['ancestors']} + Genesis 1)"
    )
    print(f"  图边数: {graph['stats']['edges']}")
    print(f"  Top 50 stake 阈值: {graph['stats']['top50_threshold_stake']:.2f} U")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    GRAPH_JSON.write_text(json.dumps(graph, ensure_ascii=False))
    print(f"  写入 {GRAPH_JSON} ({GRAPH_JSON.stat().st_size // 1024} KB)")

    # 把 graph json 安全地嵌入 HTML 的 <script type="application/json"> —— 转义 < 和 &
    safe_json = json.dumps(graph, ensure_ascii=False).replace("</", "<\\/")
    html = HTML_TEMPLATE.replace("__GRAPH_JSON__", safe_json)

    # 内置默认别名(从 raw/底池监控/地址.csv 生成)
    default_aliases: dict[str, str] = {}
    if DEFAULT_ALIASES_JSON.exists():
        try:
            default_aliases = json.loads(DEFAULT_ALIASES_JSON.read_text())
        except json.JSONDecodeError:
            print(f"  [warn] {DEFAULT_ALIASES_JSON.name} 解析失败,忽略")
    html = html.replace("__DEFAULT_ALIASES__", json.dumps(default_aliases, ensure_ascii=False))
    print(f"  内置默认别名: {len(default_aliases)} 条")

    HTML.write_text(html)
    print(f"  写入 {HTML} ({HTML.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
