# 事件管理系统 — 数据源配置 (v1.0)

> 主数据源 = **MySQL** (ai_market_data) + **4002** (TradingView UDF Backend)
> 补充 = akshare / yfinance / tvdatafeed

---

## 主数据源 1: MySQL

- 地址: 192.168.25.92:3306, 库 ai_market_data, 用户 xiao_a_user / sd123456
- 提供: 股票行情信号 (10市场, 从 ohlcv 算新高/新低/突破)
- 表: event_earnings / event_signals / event_sentiment / event_recheck_log

## 主数据源 2: 4002 (TradingView UDF Backend) — 2026-08-06 接入

- 地址: http://192.168.25.174:4002, SQLite data/tradingview.db, 869 symbols
- 提供: 商品/加密/汇率/板块/指数行情 (MySQL 没有的这些数据)

### 4002 API

| 端点                                                    | 说明                                                  |
| ------------------------------------------------------- | ----------------------------------------------------- |
| `GET /history?symbol=&resolution=&from=&to=&countback=` | K线历史（⚠️单次≤90天，超出HTTP400，内部分批10天合并） |
| `GET /quotes?symbols=A,B,C`                             | 批量实时报价                                          |
| `GET /symbols?symbol=`                                  | 品种解析                                              |
| `GET /config` / `GET /time`                             | 配置/时间                                             |

### 4002 关键标的 (覆盖 John 需求)

| 类别               | 标的                                                                                                                                    |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------- |
| 商品 CFD_COMMODITY | CFDGOLD黄金/CFDSILVER白银/COPPER铜/ALUMINIUM铝/LEAD铅/NICKEL镍/ZINC锌/NG天然气/RB汽油/CL美油/LCO布伦特/C玉米/S大豆/WHEAT小麦/COTTON棉花 |
| 加密 CRYPTO_VOL    | BTC_SPOT/ETH_SPOT/SOL_SPOT/BNB_SPOT/DOGE_SPOT/XRP_SPOT                                                                                  |
| 外汇 FOREX         | JPY_FX/CNH_FX/CHF_FX/EUR_FX/GBP_FX/CAD_FX/AUD_FX/KRW_FX/USDX_FX                                                                         |
| 国内期货 DCE       | AU0黄金/AG0白银/CU0铜/AL0铝/NI0镍/SN0锡/ZN0锌/LC0碳酸锂/C0玉米/M0豆粕/Y0豆油/CF0棉花                                                    |
| 全球指数 CFD_INDEX | TAIWAN/NIKKEI/HSI/SPX/DAX 等25个                                                                                                        |
| 板块               | INDEX_COMPLEX(128)/SP500/DAX/CAC/FTSE/ASIA_SECTOR/EURO_SECTOR/CN_SECTOR                                                                 |

### 4002 约束

- 单次查K线 ≤90天，超出 HTTP 400
- CN 品种代理可能超时30s (399239/980017 等)
- 板块分析两套系统独立

## 补充数据源

- akshare (经济日历/财报/央行利率/宏观) — 主用
- yfinance (美股财报日历/指数/商品) — 备用
- tvdatafeed (行情兜底) — 备用, 未安装

## 全球重大事件监控数据源 (2026-09-17 接入)

| 来源                | 接口                                                                                                                                                                              | 字段                                                           | 限制                                                                      |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- | ------------------------------------------------------------------------- |
| 华尔街见闻 宏观日历 | `GET https://api-one-wscn.awtmt.com/apiv1/finance/macrodatas?start=<unix>&end=<unix>`                                                                                             | importance 1-4, forecast/previous/actual, country, public_date | 单次 ≤7 天 (超出 code 60502), 分段拉                                      |
| 华尔街见闻 快讯     | `GET https://api-one.wallstcn.com/apiv1/content/lives?channel=global-channel&limit=50`                                                                                            | score (2=重要), content_text, display_time                     | 无                                                                        |
| ForexFactory        | `GET https://nfs.faireconomy.media/ff_calendar_thisweek.json` (`nextweek` 可能 404)                                                                                               | impact High/Medium/Low, date 带偏移                            | 只补漏                                                                    |
| 财联社 电报         | `GET https://www.cls.cn/v1/roll/get_roll_list?app=CailianpressWeb&os=web&sv=7.7.5&rn=50&lastTime=<unix>&sign=<md5(sha1(sorted_query))>` + `Referer: https://www.cls.cn/telegraph` | level A/B/C, reading_num, ctime                                | 签名规则来自 newsnow/RSSHub; 部分网络反爬 → `GLOBAL_EVENTS_CLS_ENABLED=0` |
| 财联社 热门         | `GET https://www.cls.cn/v2/article/hot/list?<签名参数>`                                                                                                                           | readNum, stocks                                                | 同上                                                                      |
| Google News RSS     | `GET https://news.google.com/rss/search?q=<query>+when:1d&hl=en-US&gl=US&ceid=US:en`                                                                                              | title (尾部 " - 来源"), pubDate RFC822, guid                   | 每查询 ≤100 条; 6 组固定查询见 `global_events/sources.py`                 |
| DailyHotApi (可选)  | `GET <GLOBAL_EVENTS_HOT_URL>/{weibo,baidu,toutiao}`                                                                                                                               | data[].title                                                   | 需自部署 Node 服务                                                        |

健康状态: `data/global_events_health.json` (由采集脚本写, API `sources` 字段透传)。
