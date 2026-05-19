# BiYing BubbleMap

币赢(BIYING)项目全网 referrer 关系图可视化。把 9,644 个参与地址按推荐人关系做力导向布局,Genesis 锁中心,按盈亏着色,支持搜索/筛选/导出。

🌐 **在线访问**: https://bi-ying-bubble-map.pages.dev/(部署后会更新)

## 截图

打开后类似 BubbleMaps 风格——红色亏损 / 绿色盈利 / 黄色 Genesis / 灰色疑似被盗。

## 数据口径(v6.1, 2026-05-19 23:59:59 CST 快照)

- **Stake 数据截止**: 2026-05-12 23:59:59(项目崩盘前最后一秒)
- **Unstake / 钱包到账数据截止**: 2026-05-19 23:59:59(吸收崩盘后所有补救式 unstake)
- **利息上限 CRASH_TS**: 2026-05-13 00:01(崩盘后不再计息)
- **赔付公式**: `payout = 本金 × (1 + 静态利率 × 已stake天数 / 档位天数)`
  - 1d 档静态利率 0.2245%
  - 15d 档静态利率 7.0258%
  - 30d 档静态利率 32.20%

每个地址采集了:
- 累计 stake / 累计 unstake
- 钱包总收款(含 unstake + 推荐奖 + 级差 + 节点分红;扫了 staking 主合约 + 3 个分润池)
- 应赔付总额(在押本金线性按 elapsed/30d 比例)
- 已实现盈亏(钱包总收款 − 累计 stake,**含分润**)

## 文件结构

```
.
├── index.html              # 单文件可视化(直接浏览器打开)
├── biying_aliases.json     # 内置 31 个公开别名 (紫悦/牛魔王/不具名人士A 等)
├── graph.json              # 图数据(节点 + 边),由 build_referrer_viz.py 生成
├── parent_map.json         # 9,644 child→parent 邻接表,由 build_parent_map.py 生成
└── scripts/
    ├── build_parent_map.py    # 链上抓 referrer 关系
    ├── build_referrer_viz.py  # 生成 index.html
    ├── pyrpc.py               # BSC RPC 并发工具
    └── abi_rpc.py             # ABI 编解码 + urllib RPC
```

## 重新生成

```bash
# 抓 referrer 关系(首次 ~10 分钟)
/usr/bin/python3 scripts/build_parent_map.py

# 生成 HTML
/usr/bin/python3 scripts/build_referrer_viz.py
```

需要本地有 `.env` 配置 Alchemy / Infura BSC RPC 端点(参考脚本注释)。

## 功能

- **力导向布局**:Genesis 居中,参与者按 referrer 关系自然分簇
- **配色**:绿=赚,红=亏,灰=疑似被盗,黄=Genesis
- **节点大小**:按累计 stake 取对数
- **搜索**:粘贴地址、地址前缀、或别名都能命中
- **点击节点**:右侧面板显示完整字段 + 上线链 + 下线树
- **下载**:CSV / Word / JSON 三种格式
- **别名**:
  - 🔒 私加(青色)— 只你自己可见,存 localStorage
  - 📒 内置(黄色)— 31 条 `biying_aliases.json` 烘焙进 HTML,所有人可见
- **伞下汇总**:点开任一节点能看到整棵子树聚合数据

## 数据来源

- 链上日志(Staked / Unstaked 事件)
- Alchemy `getAssetTransfers` 扫 USDT 转账(staking 合约 + 3 个分润池)
- v6.1 赔付报告:`outputs/compensation/盈亏表.csv`(主项目仓库 `币赢/` 里)

## 隐私说明

- 内置 31 个别名是**项目对外公开的角色名**(团队长 / 不具名人士 A-C 等)
- 用户在 HTML 里自己加的别名**只存在自己浏览器的 localStorage**,不会被收集、不会同步到任何服务器、其他人打开同一个 URL 看不到
- 9,644 个地址的盈亏数据基于公开链上数据(BSC),可被任何人独立验证

## License

私人项目,仅供 BIYING 项目方与持有者验算赔付盘点使用。
