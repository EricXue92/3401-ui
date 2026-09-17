#!/usr/bin/env python3
"""事件管理系统 — 数据入库模块。

把采集脚本 collect_events.py 返回的标准化事件列表写入 92 库 4 张表。
数据访问统一走 db-pool-service 中间件 (http://192.168.25.134:5050, project="3401")。
利用表的业务唯一键做幂等 INSERT (INSERT ... ON DUPLICATE KEY UPDATE)。
时间统一 HKT。错误只 print 首行。
"""
import os
import sys
import datetime

sys.path.insert(0, "/home/sdadmin/projects/db-pool-service")
sys.path.insert(1, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "external", "db-pool-service"))  # 本地开发回退
from client import DBPoolClient  # noqa: E402

# 保留兼容变量 (generate_global_events 等仍引用), 实际连库走 DBPoolClient
DB_HOST = "192.168.25.134"
DB_PORT = 5050
DB_USER = "db-pool"
DB_PASS = ""
DB_NAME = "ai_market_data"

DB = DBPoolClient(project="3401")


def _fmt(v):
    """db-pool-service 要求 datetime → ISO 字符串。"""
    if isinstance(v, datetime.datetime):
        return v.isoformat()
    if isinstance(v, datetime.date):
        return v.isoformat()
    return v


def _to_date(v):
    """db-pool 返回的日期列可能是 str 或 date/datetime, 统一转回 date。"""
    if v is None:
        return None
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    s = str(v)
    try:
        return datetime.date.fromisoformat(s[:10])
    except Exception:
        return None


def save_earnings(events):
    """写入 event_earnings。events: 列表[dict] 含 ticker/event_type/event_time/title/
    country/expected/actual/importance/source。幂等: (ticker,event_type,event_time,title)。"""
    if not events:
        return 0
    n = 0
    for e in events:
        try:
            DB.execute(
                """INSERT INTO event_earnings
                   (ticker, event_type, event_time, title, country, expected, actual, importance, source)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE actual=VALUES(actual)""",
                [e.get("ticker"), e.get("event_type"),
                 _fmt(e.get("event_time")), e.get("title"),
                 e.get("country"), e.get("expected"), e.get("actual"),
                 e.get("importance"), e.get("source")],
            )
            n += 1
        except Exception as ex:
            print(f"ERROR save_earnings: {str(ex).splitlines()[0]}")
    return n


def save_signals(signals):
    """写入 event_signals。signals: 列表[dict] 含 ticker/signal_type/signal_date/price/
    volume_ratio/consecutive_days/params_json/source。幂等: (ticker,signal_type,signal_date)。"""
    if not signals:
        return 0
    n = 0
    for s in signals:
        try:
            import json
            pj = json.dumps(s.get("params_json")) if s.get("params_json") is not None else None
            DB.execute(
                """INSERT INTO event_signals
                   (ticker, signal_type, signal_date, price, volume_ratio, consecutive_days, params_json, source)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE price=VALUES(price), volume_ratio=VALUES(volume_ratio)""",
                [s.get("ticker"), s.get("signal_type"), _fmt(s.get("signal_date")),
                 s.get("price"), s.get("volume_ratio"), s.get("consecutive_days"),
                 pj, s.get("source")],
            )
            n += 1
        except Exception as ex:
            print(f"ERROR save_signals: {str(ex).splitlines()[0]}")
    return n


def save_sentiment(events):
    """写入 event_sentiment。events: 列表[dict] 含 ticker/source/title/summary/sentiment/
    url/detected_at/dedup_key。幂等: dedup_key。"""
    if not events:
        return 0
    n = 0
    for e in events:
        try:
            DB.execute(
                """INSERT INTO event_sentiment
                   (ticker, source, title, summary, sentiment, url, detected_at, dedup_key)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE summary=VALUES(summary)""",
                [e.get("ticker"), e.get("source"), e.get("title"),
                 e.get("summary"), e.get("sentiment"), e.get("url"),
                 _fmt(e.get("detected_at")), e.get("dedup_key")],
            )
            n += 1
        except Exception as ex:
            print(f"ERROR save_sentiment: {str(ex).splitlines()[0]}")
    return n


def save_recheck_log(rows):
    """写入 event_recheck_log。rows: 列表[dict] 含 check_time/market/window_start/
    window_end/status/llm_verdict/action/note。"""
    if not rows:
        return 0
    n = 0
    for r in rows:
        try:
            DB.execute(
                """INSERT INTO event_recheck_log
                   (check_time, market, window_start, window_end, status, llm_verdict, action, note)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                [_fmt(r.get("check_time")), r.get("market"),
                 _fmt(r.get("window_start")), _fmt(r.get("window_end")),
                 r.get("status"), r.get("llm_verdict"),
                 r.get("action"), r.get("note")],
            )
            n += 1
        except Exception as ex:
            print(f"ERROR save_recheck_log: {str(ex).splitlines()[0]}")
    return n


def save_global_events(events):
    """写入 event_global。events: 列表[dict] (见 global_events 事件 dict 形状)。幂等: dedup_key。"""
    if not events:
        return 0
    n = 0
    for e in events:
        try:
            DB.execute(
                """INSERT INTO event_global
                   (kind, category, title, summary, country, event_time, importance, heat_base, heat_score,
                    reading_num, expected, previous, actual, tickers, source, source_id, url, dedup_key)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE
                     summary=VALUES(summary), importance=VALUES(importance), category=VALUES(category),
                     heat_base=VALUES(heat_base), heat_score=VALUES(heat_score),
                     reading_num=VALUES(reading_num), expected=VALUES(expected),
                     previous=VALUES(previous), actual=VALUES(actual),
                     event_time=VALUES(event_time), title=VALUES(title),
                     url=VALUES(url), country=VALUES(country), tickers=VALUES(tickers)""",
                [e.get("kind"), e.get("category"), e.get("title"), e.get("summary"), e.get("country"),
                 _fmt(e.get("event_time")), int(e.get("importance") or 1),
                 float(e.get("heat_base") or 0), float(e.get("heat_score") or 0),
                 e.get("reading_num"), e.get("expected"), e.get("previous"), e.get("actual"),
                 e.get("tickers"), e.get("source"), e.get("source_id"), e.get("url"), e.get("dedup_key")],
            )
            n += 1
        except Exception as ex:
            print(f"ERROR save_global_events: {str(ex).splitlines()[0]}")
    return n
