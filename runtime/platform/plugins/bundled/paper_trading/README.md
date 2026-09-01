# paper_trading · 模拟炒股插件

可插拔、**带页面**的 echo 插件模块:内置 A 股股票池 + 盘中随机游走模拟行情,
提供一套完整的前端交易练习面板(行情 / 买卖 / 持仓 / 成交 / K线)。

- **「交易」页** 交易全部为**本地模拟**,绝不下单到真实平台、不涉及真实资金。
- **「平台交易」页**(新增)直接使用**平台配资盘**账户:申请资金、合约、持仓、
  以及真实(平台模拟)买卖委托——订单真实提交到你的平台账号。所有真实操作都有**二次确认**。
- 行情页顶部 **live_mode** 只读接入平台后端,展示**真实大盘**
  (指数、全市场涨跌家数、市场状态)。

## 页面

启动 echo 后,浏览器打开:

```
http://<echo地址>/api/plugins/paper-trading/page
```

- 行情页:30 只 A 股模拟报价,可搜索、点击行看日 K,买/卖打开下单面板;每行 ☆ 可一键加自选
  - 顶部「平台实时大盘」:开启 live_mode 且配置好账号后,展示真实指数(K 线小图)+ 涨跌家数 + 市场状态
- 交易终端(仿平台交易界面):左侧股票列表 + 中间分时/日K 图 + 右侧「报价指标 + 十档盘口 + 买入/卖出面板」,
  支持 市价/限价、1/4·1/3·半仓·全仓 快捷仓位、涨停/跌停、可买/可卖提示;下方持仓/成交明细
- **盯盘**(新增):紧凑真实行情面板(`/api/plugins/paper-trading/watch`,工作台「模拟炒股」页有
  「平台原版 / 盯盘」两个 tab)——大盘指数 + 全市场涨跌家数 + **平台持仓** + **平台自选**,
  全部真实行情,每 4 秒自动刷新,涨跌提醒阈值(≥1%/2%/3%/5%/7%)高亮触发行;切到后台自动暂停轮询
- **每日签到**:顶栏直接显示今日状态、利息券奖励和自动签到开关；自动任务按上海时区执行，
  先查状态、未签到才提交，重复调用按“今日已签”幂等处理，不触碰任何交易接口
- **平台交易**:对接**平台配资盘**——
  - 合约管理:显示平台合约(可用资金 / 总交易额),支持**申请资金**(按天/按周/按月 × 保证金档位 × 倍数)、**追加资金**、**提盈**
  - 真实买卖:选合约 + 股票代码/名称 + 限价/市价 + 数量,提交后弹窗**二次确认**,确认才真正向平台提交委托
  - 平台持仓(成本/现价/市值/浮动盈亏,可一键预填卖出)+ 平台委托记录(最近)
- 自选页:自选股分组管理 —— 默认自选组 + 自定义分组,支持新建 / 双击重命名 / 删除分组,
  分组内增删股票、现价涨跌一目了然;持久化到 `watchlists.json`,重置账户不清空
- 交易:市价/限价、整手(100 股)、T+1(当日买入次日可卖)、佣金万3(最低 5 元)+ 卖出印花税 0.05%
- 持仓页:成本价 / 现价 / 市值 / 盈亏
- 成交页:成交历史(最近 200 条)
- 涨跌停 ±10%;非交易时段行情冻结、不能下单(周一~周五 9:30-11:30 / 13:00-15:00)

## API(前缀 `/api/plugins/paper-trading/`)

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/page` | 前端页面 |
| GET | `/live/overview` | 平台实时大盘(只读;未启用/失败时 `available: false`) |
| GET | `/live/watch` | 盯盘聚合:大盘 + 平台持仓 + 平台自选(只读;短 TTL 缓存,单源失败只降级对应字段) |
| GET | `/watch` | 盯盘页面(独立 HTML) |
| GET | `/live/status` | 平台连接状态(是否启用/是否已配置凭证/账号) |
| POST | `/live/credentials` | 保存平台账号凭证 `{phone, password}`(落盘 chmod 600,自动验证登录) |
| POST | `/live/credentials/clear` | 清除已保存的平台凭证 |
| POST | `/live/refresh` | 强制刷新实时大盘(绕过缓存) |
| GET | `/check-in/status` | 今日签到、连续天数、利息券余额与自动任务状态 |
| GET | `/check-in/config` | 官方签到奖励规则(只读) |
| POST | `/check-in` | 手动签到 `{confirm:true}`；日期由后端按上海时区生成 |
| POST | `/check-in/schedule` | 开关/设置自动签到 `{enabled,hour,minute}` |
| GET | `/quotes/status` | 统一行情中心状态：主/备用源、数据新鲜度、订阅并集、容量与切换原因；查询不会创建上游连接 |
| GET | `/quotes/snapshot?codes=` | 按代码读取统一快照，字段含 `source/source_ts/received_at/seq/stale` |
| GET | `/quotes/stream?codes=` | 新版 SSE 行情流；连接建立/断开自动增减引用，每连接最多 100 只 |
| GET | `/live/push/status` | 实时推送连接状态(WS 是否在连、各事件订阅/最近推送) |
| GET | `/live/push/subscribe?event=&codes=` | 旧版兼容入口；个股行情内部转入统一行情中心，不再覆盖其他用户订阅 |
| GET | `/live/push/latest?event=&light=` | 查询某事件最新快照(量化策略按需取价) |
| GET | `/live/push/stream?light=` | **SSE 实时推送流**:平台行情逐条推给浏览器(替代轮询) |
| GET | `/platform/status` | 平台连接状态(是否登录/账号) |
| GET | `/platform/overview` | 平台会员信息 + 合约列表 |
| GET | `/platform/contracts` | 合约列表(只读) |
| GET | `/platform/positions` | 平台持仓(只读) |
| GET | `/platform/orders?type=&current=&size=` | 平台委托记录(只读) |
| GET | `/platform/rate-table` | 配资费率表(只读) |
| GET | `/platform/apply-options` | 申请资金档位(只读) |
| GET | `/platform/sell-panel` | 卖出面板/可卖数量(只读) |
| POST | `/platform/apply-contract` | 申请资金 `{contract_type, principal, multiple, confirm}` |
| POST | `/platform/buy` | 真实买入 `{contract_id, stock_code, stock_name, entrust_type, price, qty, confirm}` |
| POST | `/platform/sell` | 真实卖出(同上) |
| POST | `/platform/add-capital` | 追加资金 `{contract_id, money, confirm}` |
| POST | `/platform/withdraw-profit` | 提盈 `{contract_id, money, confirm}` |
| POST | `/platform/cancel-order` | 撤单 `{order_id, contract_id, confirm}` |
| GET | `/symbols` | 股票池 |
| GET | `/quotes` | 全部模拟报价 |
| GET | `/quote/{code}` | 单只报价 |
| GET | `/kline/{code}?days=60` | 合成日 K |
| GET | `/orderbook/{code}?levels=10` | 十档盘口(模拟买/卖各 10 档) |
| GET | `/account` | 资金 / 持仓 / 盈亏 |
| GET | `/orders` | 成交历史 |
| POST | `/orders` | 下单 `{code, side: buy\|sell, order_type: market\|limit, price?, qty}` |
| GET | `/watchlists` | 自选分组列表(含现价) + 股票池 |
| POST | `/watchlists` | 新建分组 `{name}` |
| PATCH | `/watchlists/{gid}` | 重命名分组 `{name}` |
| DELETE | `/watchlists/{gid}` | 删除分组 |
| POST | `/watchlists/{gid}/stocks` | 分组加股票 `{code}` |
| DELETE | `/watchlists/{gid}/stocks/{code}` | 分组移除股票 |
| POST | `/watchlists/fav` | 行情页 ☆ 切换自选 `{code}`(加入默认组 / 全部移除) |
| POST | `/reset` | 重置账户到初始资金 |

## Skill

- `paper_trading.quote` —— agent 可查询任意一只内置股票的模拟报价(只读,始终可用)。
- `paper_trading.live_quotes` —— 从统一行情中心读取真实行情快照；仅在配置了可信
  HTTPS 行情入口时注册，`stale=true` 的报价禁止用于交易或自动信号。
- `paper_trading.trade` —— agent 平台**自动下单**(买入/卖出/申请资金/追加资金/提盈/撤单),
  仅当 `auto_trade: true` 时注册可用;默认关闭。

## 平台实时大盘(live_mode)

默认关闭(`plugin.yaml` → `config.live_mode: false`)。只在可信的单用户本地环境中显式
开启后，才会通过配置的可信 HTTPS `base_url` 拉取平台行情。HTTP、跳转响应以及
认证/多用户宿主都会 fail-closed：

- 登录:`POST /api/member/member/login`(RSA-1024 加密密码,与对方 App 一致),JWT 缓存到
  `~/.echo/data/paper_trading/token.json`,过期自动重登。
- 行情:`POST /api/market/v2/data/doAction?event=todayStock` → gzip 解压出真实指数 + 涨跌家数。

### 统一行情中心(WS 主源 + 批量快照备用)

平台在**交易时段持续推送**行情(非轮询、非截图)。QuoteHub 把所有浏览器/Agent 的代码
去重成一个并集，只维持一份上游订阅，然后按用户过滤分发；新增用户不会新增一条上游
连接。主源是 WSS WebSocket(socket.io v2 / engine.io v3)，同平台批量 REST 快照只在
主源故障后接管。不会从 HTTPS 自动降级到明文 WS：

- 握手:`/socket.io/?EIO=3&source=h5&sign=<getSignString(1234)>&transport=websocket`。
- 订阅事件:
  - `kLineRealTime` — 个股实时报价 + 十档盘口 + 分时(参数为代码列表,如 `605080.sh,003032.sz`);
  - `todayStock` — 大盘(指数 + 涨跌家数 + 市场状态);
  - `stockPosition` — 持仓推送;`itemByStepDetailsV3` — 分时/盘口详情。
- 断线自动重连(重取 token / 重新签名 / 重订阅全站代码并集),推送数据 gzip 自动解压。
- 交易时段连续 3 次静默/失败后切换备用源；主源连续恢复且稳定 120 秒后才回切，避免抖动。
- 每个下游队列最多保留 50 批；慢客户端自动丢旧留新，不会无限占用内存。
- 默认保护线：每连接 100 只、同时 50 个行情连接、全站去重后 500 只，适合 2G 服务器。
- `stale=true` 的旧报价只供查看，不触发页面提醒、Agent 自动信号或模拟成交。
- `light=true`(默认)时 `kLineRealTime` 会压缩为紧凑字段:code/name/price/change_pct/
  open/high/low/prev_close/volume/amount/换手/bids/asks,省流量。

**Python 内嵌 API(Agent/策略)** —— 读取统一快照，不直接创建第二条 WS:

```python
plugin = get_plugin("paper_trading")
snapshot = plugin.quote_snapshot(["605080.sh", "003032.sz"])
status = plugin.quote_status()
```

**HTTP/SSE**:盯盘页直接连接 `/quotes/stream?codes=...`，首包是 `snapshot`，后续为
`quote/status`；断开时只轮询本机 `/quotes/snapshot`，不会让每个浏览器各自请求上游。
页面按 150ms 合并重绘、忽略乱序 `seq`，隐藏或关闭页面时主动退订。

通达信和腾讯自选股当前 MCP 都是查询式接口，不是行情 WebSocket；它们尚未作为默认热备
启用。后续接入时应使用独立的服务器级 provider 批量轮询，不能在后台复用某个用户的
MCP 凭证。

> 这是个人练习用途:只读真实行情供量化练习,下单仍是页面/确认式。行情仅供参考,不构成投资建议。

### 平台交易(真实操作,二次确认)

「平台交易」页复用同一套登录凭证(见下)。登录后:

- 所有**写操作**(申请资金 / 买入 / 卖出 / 追加资金 / 提盈)在页面都需**二次确认**:
  先填表单 → 弹「平台真实下单确认」→ 核对合约/方向/股票/价格/数量 → 确认才提交。
- 后端也强制校验:每个写接口都要求请求体带 `confirm: true`,否则直接返回
  `{ok:false, error:"已拦截:该操作将在平台真实执行…"}`,**绝不在缺少确认时调平台**。
- 只读接口(合约/持仓/委托/费率/档位/卖出面板)失败会优雅降级 `{ok:false, error}`,不影响页面。
- 平台请求只允许 HTTPS/WSS，且拒绝重定向；配资盘操作仍只应在可信单用户环境使用。

### 自动交易(agent/程序化下单,`auto_trade`)

默认 `auto_trade: false`(plugin.yaml → config),此时**所有真实下单只能人工在页面二次确认**;
插件也不会向 agent 暴露下单 skill,防误触。

把 `auto_trade` 改为 `true` 并重启后:

- 插件注册 **`paper_trading.trade`** skill,agent 可以直接向平台**自动下单**
  (买入/卖出/申请资金/追加资金/提盈/撤单),相当于开启程序化交易授权。
- skill 内部自带 `confirm` 授权(开启即代表用户授权),但建议先用 `dry_run=true` 试运行,
  只返回执行计划、不下单。
- 参数:`action` 必填(`buy/sell/apply/add_capital/withdraw/cancel`)+ 对应字段;
  数量必须 100 整数倍,限价单必须带 `price`。
- 页面「平台交易」页状态栏会显示「自动交易:开/关」,随时可见当前授权状态。

> 自动交易有风险:策略/参数写错会直接把单打到平台账户(模拟盘)。建议开启后先用小额 + dry_run 验证。

### 每日自动签到

平台 PC 网页只展示“我的利息券”，没有签到按钮；插件顶栏补充了完整入口。签到使用平台
官方接口，提交参数固定为后端生成的上海时区当天日期，不能指定过去或未来日期。每次提交前后
都会重新查询状态：当天已签直接返回成功，不重复写入。

- 页面默认时刻为每天 `08:05`（Asia/Shanghai），点击“开启自动签到”后保存到本机；
- 插件启动时会补查一次，避免电脑错过 08:05 后当天不再签到；临时网络失败每 15 分钟重试；
- 只读取已有 `token.json`，不会为了签到读取或发送账号密码；登录态失效时提示重新登录；
- 当前上游只提供 HTTP，因此签到仅限可信单用户本地实例；认证、多用户部署与原站代理关闭时不启用。

### 登录界面(页面内)

页面「平台实时大盘」卡片右上角有 **「登录」** 按钮,点击弹出登录框:

- 输入平台的**手机号 + 密码**,点「登录并连接」,插件会保存凭证并立刻验证登录;
  验证通过后顶部即显示真实大盘(账号名如 `HL51550949`)。
- 凭证只保存在本机 `~/.echo/data/paper_trading/credentials.json`(**chmod 600**),
  页面不会回显密码;也可用「清除凭证」按钮删掉。
- 不用页面时,也可用环境变量 `PAPER_TRADING_PHONE` / `PAPER_TRADING_PASSWORD` 配置。
- 凭证保存后会写本地文件(个人练习便利)；认证、多用户或公网部署会关闭整个账户集成。
- 无凭证 / 网络异常 / 登录失败时**自动降级**:页面照常显示本地模拟行情,实时大盘区块显示「未连接」。
- 请求按 `live_ttl`(默认 30s)合并缓存,避免高频打对方后端。

> 这是个人练习用途,只读真实行情、不做真实交易;行情仅供参考,不构成投资建议。

### 平台原版网页(本地默认开启)

可信的单用户本地实例默认挂载同源反向代理，工作台会直接加载平台原站。当前示例平台
只提供明文 HTTP，因此原站代理接受经过严格规范化的 HTTP(S) 地址；路径白名单、宿主
凭证剥离和请求大小限制仍然生效。`live_mode` 与 `auto_trade` 不受此默认值影响，继续
保持关闭并严格要求 HTTPS。

可将 `proxy_origin` 或 `allow_same_origin_third_party_scripts` 显式设为 `false` 来关闭原站
代理。认证、多用户或公网部署会强制禁用该代理，因为第三方脚本会获得本应用 origin 的
权限；使用 HTTP 上游时，传输内容也不具备加密保护。

### 复刻版 → 插件中心

原先我们自研的 **1:1 复刻交易页** 已从本插件拆出,作为独立插件
**`paper_trading_replica`(模拟炒股 · 复刻版)** 停放在插件中心,暂不在工作台前端展示,
留待后续继续打磨。启用方式:插件中心启用 `paper_trading_replica`,页面在
`/api/plugins/paper-trading-replica/page`,复用本插件后端 API(行情/自选/平台配资交易)。

## 数据与状态

- 账户 / 持仓 / 成交记录持久化到 `~/.echo/data/paper_trading/state.json`,重启不丢。
- 自选股分组持久化到 `~/.echo/data/paper_trading/watchlists.json`(与账户独立,重置账户不清空)。
- 自动签到设置/最近结果分别保存在 `auto_sign_in.json` 与 `auto_sign_in_status.json`。
- 初始资金可在 `plugin.yaml` 的 `config.initial_cash` 调整(默认 100 万模拟资金)。
- 行情为随机游走合成数据,每次刷新在交易时段内小幅波动。

## 说明

「交易」页的行情与成交为本地模拟,不对应真实市场,不构成投资建议。
「平台交易」页会把你确认的操作真实提交到平台配资盘账户(平台侧为模拟盘),请仔细核对后再确认。
