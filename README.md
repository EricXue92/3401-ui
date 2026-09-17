# 3401 事件管理系統 (時間地圖/事件日曆)

Flask 獨立服務，端口 **3401**。提供事件日曆 + 實時監控 + 盤前重點 + 每日回顧。

## 快速啟動

```bash
cd event-management-system
pip install flask requests pandas yfinance
python3 server.py
# 訪問 http://127.0.0.1:3401/
```

## 依賴

- **Python 3.10** + flask, requests, pandas, yfinance
- **db-pool-service** 中間件（`.134:5050`，project=3401）→ 連接 92 庫 `ai_market_data`
- **cross_market_link.py**（`/home/sdadmin/projects/cross-market-monitor/`）→ 板塊↔商品映射

## 數據源

| 來源               | 端點                                                      | 用途                                                                           |
| ------------------ | --------------------------------------------------------- | ------------------------------------------------------------------------------ |
| db-pool-service    | `.134:5050`                                               | 92庫讀寫（event_earnings / event_signals / stock_liquidity / market_sessions） |
| 3400               | 本機                                                      | 放量排名（盤前重點）                                                           |
| 3402               | 本機                                                      | 異常放量板塊                                                                   |
| 3403               | 本機                                                      | swap-klines 即時行情（52 週高低）                                              |
| 3404               | 本機                                                      | 持續放量商品                                                                   |
| 4002               | `.174:4002`                                               | TradingView UDF（商品/加密/匯率/債券行情）                                     |
| 華爾街見聞         | `api-one-wscn.awtmt.com` / `api-one.wallstcn.com`         | 全球宏觀日曆 (importance 1-4) + 7x24 快訊 → event_global                       |
| ForexFactory       | `nfs.faireconomy.media/ff_calendar_thisweek.json`         | 本週宏觀日曆補漏 (High/Medium)                                                 |
| 財聯社             | `www.cls.cn` (需簽名, `GLOBAL_EVENTS_CLS_ENABLED=0` 可關) | 電報 + 熱門文章                                                                |
| Google News RSS    | `news.google.com/rss/search`                              | 英文媒體 24h 報道數 (熱度)                                                     |
| DailyHotApi (可選) | `GLOBAL_EVENTS_HOT_URL`                                   | 微博/百度/頭條熱榜 → 社媒同現加分                                              |

## 配置

`config.py` 集中管理所有上游服務地址，支持環境變量覆蓋：

```python
SRV_3400 = os.environ.get("SRV_3400_URL", "http://127.0.0.1:3400")  # 本機
SRV_3402 = os.environ.get("SRV_3402_URL", "http://127.0.0.1:3402")  # 本機
SRV_3403 = os.environ.get("SRV_3403_URL", "http://127.0.0.1:3403")  # 本機
SRV_3404 = os.environ.get("SRV_3404_URL", "http://127.0.0.1:3404")  # 本機
SRV_4002 = os.environ.get("TV4002_URL", "http://192.168.25.174:4002") # 集中
SRV_3401 = os.environ.get("SRV_3401_URL", "http://127.0.0.1:3401")   # 自身
```

> 34 系列面板在每台機本機各跑一份，`127.0.0.1` 通用。db-pool(5050) 和 4002 是集中服務。

## Cron 定時任務

| #   | 時間            | 腳本                                          | 作用                        | 輸出                       |
| --- | --------------- | --------------------------------------------- | --------------------------- | -------------------------- |
| 1   | 每 30 分鐘      | `scripts/recheck_events.py`                   | 複查事件完整性              | → DB event_recheck_log     |
| 2   | 18:00 (週1-5)   | `scripts/collect_events.py --economic`        | 採集財經日曆                | → DB event_signals         |
| 3   | 18:05 (週1-5)   | `scripts/collect_events.py --cn-earnings`     | 採集 A 股財報               | → DB event_earnings        |
| 4   | 04:35 (每天)    | `scripts/generate_global_events.py`           | 全球事件信號                | → DB event_signals         |
| 5   | 16:30 (週1-5)   | `scripts/run_signals_all.sh`                  | 亞洲市場信號採集            | → DB event_signals         |
| 6   | 04:30 (週2-6)   | `scripts/run_signals_all.sh`                  | 歐美市場信號採集            | → DB event_signals         |
| 7   | 06:00 (每隔3天) | `scripts/collect_us_earnings_future.py`       | 採集美國未來財報            | → DB event_earnings        |
| 8   | 06:00 (每天)    | `scripts/collect_daily_review.py`             | 生成每日回顧快照            | → data/daily_review/*.json |
| 9   | 每 2 分鐘       | `keepalive.sh`                                | 進程保活（探活端口 3401）   | → 自動重啟 server.py       |
| 10  | 每 30 分鐘      | `scripts/collect_global_events.py --calendar` | 全球已排程事件 (未來 14 天) | → DB event_global          |
| 11  | 每 3 分鐘       | `scripts/collect_global_events.py --breaking` | 全球突發快訊 + 熱度         | → DB event_global          |

### 安裝 Cron

```bash
# 查看現有 cron
crontab -l

# 添加 3401 cron（復制以下內容）
crontab -l | {
  cat
  echo "*/30 * * * * cd /home/sdadmin/.openclaw/workspace/projects/event-management-system/scripts && python3 recheck_events.py >> /tmp/event_map/recheck.log 2>&1"
  echo "0 18 * * 1-5 cd /home/sdadmin/.openclaw/workspace/projects/event-management-system/scripts && python3 collect_events.py --economic \$(date +\\%Y\\%m\\%d) >> /tmp/event_map/economic.log 2>&1"
  echo "5 18 * * 1-5 cd /home/sdadmin/.openclaw/workspace/projects/event-management-system/scripts && python3 collect_events.py --cn-earnings >> /tmp/event_map/cn_earnings.log 2>&1"
  echo "35 4 * * * cd /home/sdadmin/.openclaw/workspace/projects/event-management-system/scripts && python3 generate_global_events.py >> /tmp/event_map/global_events.log 2>&1"
  echo "30 16 * * 1-5 bash /home/sdadmin/.openclaw/workspace/projects/event-management-system/scripts/run_signals_all.sh >> /tmp/event_map/signals.log 2>&1"
  echo "30 04 * * 2-6 bash /home/sdadmin/.openclaw/workspace/projects/event-management-system/scripts/run_signals_all.sh >> /tmp/event_map/signals.log 2>&1"
  echo "0 6 */3 * * cd /home/sdadmin/.openclaw/workspace/projects/event-management-system/scripts && python3 collect_us_earnings_future.py >> /tmp/event_map/us_earnings.log 2>&1"
  echo "0 6 * * * cd /home/sdadmin/.openclaw/workspace/projects/event-management-system/scripts && PYTHONPATH=/home/sdadmin/.local/lib/python3.10/site-packages /usr/bin/python3.10 collect_daily_review.py \$(date -d yesterday +\\%F) >> /tmp/event_map/daily_review.log 2>&1"
  echo "*/2 * * * * bash /home/sdadmin/.openclaw/workspace/projects/event-management-system/keepalive.sh >> /tmp/event_map/keepalive.log 2>&1"
  echo "*/30 * * * * cd /home/sdadmin/.openclaw/workspace/projects/event-management-system/scripts && python3 collect_global_events.py --calendar >> /tmp/event_map/global_events.log 2>&1"
  echo "*/3 * * * * cd /home/sdadmin/.openclaw/workspace/projects/event-management-system/scripts && python3 collect_global_events.py --breaking >> /tmp/event_map/global_events.log 2>&1"
} | crontab -
```

## 數據目錄

```
data/
├── daily_review/          # 每日回顧快照（cron #8 產生，每天一條 JSON）
├── volume_events/         # 異常放量事件歷史（cron 產生）
└── watch_52w.json         # 52 週高低位置（calc_index_positions 輸出）
```

> 這些是可再生快取，`.144` 部署時不 rsync 也行，cron 跑一輪自動重建。

## 部署到 .144 (Live)

```bash
# 1. 克隆代碼
cd /home/sdadmin/system
git clone https://github.com/openclawff/3401-event-management-system.git

# 2. 建虛擬環境 + 裝依賴
cd event-management-system
python3 -m venv venv
source venv/bin/activate
pip install flask requests pandas yfinance

# 3. 安裝 cross_market_link.py（從 .134 複製）
scp sdadmin@192.168.25.134:/home/sdadmin/projects/cross-market-monitor/cross_market_link.py .

# 4. 安裝 db-pool client（從 .134 複製）
scp sdadmin@192.168.25.134:/home/sdadmin/projects/db-pool-service/client.py .

# 5. 安裝 cron（復制上面的 crontab 內容）

# 6. 啟動
python3 server.py
```

## API 端點

| 端點                                    | 用途                          |
| --------------------------------------- | ----------------------------- |
| `GET /api/event_map/health`             | 健康檢查 + 表行數             |
| `GET /api/event_map/calendar?date=`     | 當日事件日曆（v2 核心）       |
| `GET /api/event_map/watch`              | 實時監控（52 週突破位）       |
| `GET /api/event_map/assets`             | 資產狀態（右欄）              |
| `GET /api/event_map/market_status`      | 市場交易狀態                  |
| `GET /api/event_map/economic?date=`     | 經濟數據日曆                  |
| `GET /api/event_map/daily_review?date=` | 每日回顧快照                  |
| `POST /api/events`                      | 接收外部推送事件（3404 放量） |

## 項目結構

```
event-management-system/
├── server.py              # Flask 後端（1,480 行，全部 API）
├── db.py                  # 數據庫中間件（DBPoolClient → .134:5050）
├── config.py              # 上游服務統一配置
├── keepalive.sh           # 進程保活腳本
├── templates/
│   └── event_map.html     # 前端模板（668 行）
├── scripts/
│   ├── recheck_events.py
│   ├── collect_events.py
│   ├── collect_us_earnings_future.py
│   ├── generate_global_events.py
│   ├── calc_index_positions.py
│   ├── collect_daily_review.py
│   ├── db_writer.py       # 寫入辅助（DB.execute）
│   └── run_signals_all.sh
├── data/                  # 落盤快取（不入 git）
├── docs/                  # 文檔
└── .gitignore
```

## 全球重大事件監控 (2026-09-17)

首頁「🌐 全球重大事件監控」板塊。建表: 執行 `scripts/schema.sql` 中 `event_global` 段 (需 DB 管理方確認)。
部署後先跑 `python3 scripts/collect_global_events.py --check` 看各來源連通性, 再 `--all` 初始化。
財聯社若被反爬: 環境變量 `GLOBAL_EVENTS_CLS_ENABLED=0`。社媒熱榜可選: 部署 DailyHotApi
(`docker run -d --name dailyhot -p 6688:6688 imsyy/dailyhot-api`) 後設 `GLOBAL_EVENTS_HOT_URL=http://127.0.0.1:6688`。
人工維護: `global_events/static/cn_policy_events.json` (中國政策會議)、`global_events/static/mega_caps.json` (萬億市值白名單)。
