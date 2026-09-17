# -*- coding: utf-8 -*-
"""
事件管理系统 (时间地图) — API 层服务
独立 Flask 服务, 端口 3401。与 3400 (sector-stocks-fullmap-panel) 完全独立。
端点:
  GET /api/event_map/health         健康检查 + 各事件表行数
  GET /api/event_map/timeline?date= 指定交割日全部事件 (HKT 06:00 -> 次日 06:00)
  GET /api/event_map/earnings?date=&tier=  财报列表(带流动性级别, 未来7天, 可L1-L5过滤)
  GET /api/event_map/economic?date= 经济数据/央行日历
  GET /api/event_map/assets         资产状态 (指数/汇率/商品)
  GET /api/event_map/market_status  市场交易状态
  GET /api/event_map/global_events?days= 全球重大事件 (今日按热度 + 未来 N 天日历)
"""
import json
import os
import sys
import threading
import urllib.request
from datetime import datetime, date, time, timedelta

from flask import Flask, jsonify, request, Response

import db
import config

# 跨市场板块→商品映射 (来源: 3413 相关性规则)
sys.path.insert(0, '/home/sdadmin/projects/cross-market-monitor')
sys.path.insert(1, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'external', 'cross-market-monitor'))  # 本地开发回退
from cross_market_link import SECTOR_LINK_MAP

# ── 异常放量列缓存 (3402/3404 上游慢, 避免每次 calendar 请求被拖慢) ──
# 3402 /api/volume-surge 固定 ~40s 无内部缓存; 3404 秒回。
# 方案: 后台线程预热 + 模块级缓存 (TTL=300s), calendar 端点永远秒读缓存。
_surge_cache = {"ts": 0.0, "data": []}
_SURGE_CACHE_TTL = 300.0
_surge_computing = False
_surge_lock = threading.Lock()

def _surge_refresh():
    """后台线程: 拉取 3402/3404 并交叉匹配, 填充 _surge_cache."""
    global _surge_computing
    try:
        import time as _t
        surge_sectors = []
        try:
            _vol_req = urllib.request.Request(
                f"{config.SRV_3402}/api/volume-surge",
                headers={"User-Agent": "Mozilla/5.0"})
            _vol_resp = json.loads(urllib.request.urlopen(_vol_req, timeout=50).read())

            _sus_req = urllib.request.Request(
                f"{config.SRV_3404}/api/sustained_surges",
                headers={"User-Agent": "Mozilla/5.0"})
            _sus_resp = json.loads(urllib.request.urlopen(_sus_req, timeout=15).read())

            _sustained = {s["symbol"]: s for s in _sus_resp.get("sustained", [])}

            for _mkt in ("cn", "hk", "us", "eu"):
                _anomalies = _vol_resp.get(_mkt, {}).get("anomalies", [])
                for _anom in _anomalies:
                    _sector = _anom.get("sector", "")
                    _link = SECTOR_LINK_MAP.get(_sector)
                    if not _link:
                        continue
                    for _sym in _link["linked_symbols"]:
                        _st = _sustained.get(_sym)
                        if not _st:
                            continue
                        _windows = _st.get("surge_windows", [])
                        _windows_str = ", ".join(
                            f"{w[0]}-{w[1]}h" for w in _windows if isinstance(w, (list, tuple)) and len(w) >= 2
                        ) if _windows else ""
                        surge_sectors.append({
                            "market": _mkt,
                            "sector": _sector,
                            "sector_direction": _anom.get("direction", ""),
                            "sector_ratio": _anom.get("avg_ratio"),
                            "symbol": _sym,
                            "group": _st.get("group", ""),
                            "hours_sustained": _st.get("hours_sustained"),
                            "surge_windows": _windows_str,
                            "sustained_direction": _st.get("direction", ""),
                            "correlation": _link.get("correlation", ""),
                            "source": _link.get("source", ""),
                        })
            _surge_cache["data"] = surge_sectors
            _surge_cache["ts"] = _t.time()
        except Exception as _e:
            print(f"[WARN] surge_sectors fetch failed: {_e}")
    finally:
        _surge_computing = False

def _get_surge_sectors():
    """读缓存; 缓存过期/为空时后台刷新并立即返回当前缓存 (不阻塞请求)."""
    global _surge_computing
    import time as _t
    now = _t.time()
    if _surge_cache["data"] and (now - _surge_cache["ts"]) < _SURGE_CACHE_TTL:
        return _surge_cache["data"]
    with _surge_lock:
        if not _surge_computing:
            _surge_computing = True
            threading.Thread(target=_surge_refresh, daemon=True).start()
    return _surge_cache["data"]  # 立即返回当前缓存 (可能为空, 后台填充后刷新页面可见)

# 服务启动即后台预热一次
threading.Thread(target=_surge_refresh, daemon=True).start()

app = Flask(__name__)

# CORS — 允许前端跨域读取 (file:// 或其它端口)
@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp

def _as_date(v):
    """db-pool 返回日期列是 str, 轉為 datetime.date."""
    if isinstance(v, date):
        return v
    if isinstance(v, datetime):
        return v.date()
    return date.fromisoformat(str(v))

import re as _re
def _parse_dur(s):
    """解析 ISO 8601 duration 'PT9H30M' → timedelta. db-pool 返回 str, pymysql 返回 timedelta."""
    if isinstance(s, timedelta):
        return s
    m = _re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', str(s))
    if not m:
        return timedelta()
    return timedelta(hours=int(m.group(1) or 0), minutes=int(m.group(2) or 0), seconds=int(m.group(3) or 0))

# 4002 数据源 (TradingView UDF Backend) — 补充商品/加密/汇率/指数行情
TV4002 = config.SRV_4002


def tv4002_quotes(symbols):
    """批量实时报价: /quotes?symbols=A,B,C → {sym: {lp,chp}}"""
    try:
        if not symbols:
            return {}
        url = f"{TV4002}/quotes?symbols=" + ",".join(symbols)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read().decode())
        out = {}
        for q in d.get("d", []):
            if q.get("s") == "ok":
                v = q.get("v", {})
                out[q["n"]] = {"lp": v.get("lp"), "chp": v.get("chp")}
        return out
    except Exception as e:
        print(f"ERROR: tv4002_quotes {str(e).splitlines()[0]}")
        return {}
app.config["JSON_AS_ASCII"] = False  # 中文 UTF-8 直出
app.json.ensure_ascii = False

def _yf_symbol_for(ym, name):
    """把 4002 symbol/显示名 映射到 yfinance 指数符号"""
    m = {
        "SPTRD": "^GSPC", "NASD": "^NDX", "DOW": "^DJI", "RUSSELL": "^RUT",
        "NIKKEI": "^N225", "TOPIX": "^TOPX", "HSI": "^HSI", "HHI": "^HSCE",
        "HTI": "^HSTECH", "TAIWAN": "^TWII", "KOSPI": "^KS11", "ASX": "^AXJO",
        "SENSEX": "^BSESN", "FTSE": "^FTSE", "DAX": "^GDAXI", "CAC": "^FCHI",
        "SMI": "^SSMI", "AEX": "^AEX", "CN50": "000300.SS", "STXE": "^STOXX50E",
        "IBEX": "^IBEX", "IT40": "FTSEMIB.MI",
    }
    return m.get(ym)


def _yf_symbol_for_asset(sym):
    """把 4002 symbol 映射到 yfinance ticker（商品/加密/汇率/债券）"""
    m = {
        # 贵金属
        "CFDGOLD": "GC=F", "CFDSILVER": "SI=F", "XPTUSD": "PL=F", "XPDUSD": "PA=F",
        # 基本金属
        "COPPER": "HG=F", "ALUMINIUM": "ALI=F", "NICKEL": "NI=F",
        "ZINC": "ZNC=F", "LEAD": "LEA=F", "TIN": "TIN=F", "HG": "HG=F",
        # 能源
        "CL": "CL=F", "LCO": "BZ=F", "NG": "NG=F", "RB": "RB=F",
        # 农产品
        "WHEAT": "ZW=F", "C": "ZC=F", "S": "ZS=F", "COTTON": "CT=F",
        "SBO": "ZL=F", "SM": "ZM=F",
        # 加密
        "BTC_SPOT": "BTC-USD", "ETH_SPOT": "ETH-USD", "SOL_SPOT": "SOL-USD",
        # 汇率 (兑美元 ETF 近似)
        "JPY_FX": "JPY=X", "CNH_FX": "CNH=X", "CHF_FX": "CHF=X",
        "EUR_FX": "EURUSD=X", "GBP_FX": "GBPUSD=X", "CAD_FX": "CAD=X",
        "AUD_FX": "AUDUSD=X", "NZD_FX": "NZDUSD=X", "USDX_FX": "DX-Y.NYB",
        # 债券
        "US_BOND": "TLT", "EURO_BOND": "IEUR", "CN_BOND": "511010.SS",
        "JP_BOND": "BNDX",
    }
    return m.get(sym)


def _yf_history_52w(sym, name=''):
    """yfinance 拉52周（或最长可用）日线, 返回 {high52, low52, latest, bars, src}"""
    try:
        import yfinance as yf
        yf_sym = _yf_symbol_for_asset(sym) or _yf_symbol_for(sym, name)
        if not yf_sym:
            return None
        df = yf.download(yf_sym, period="1y", interval="1d", progress=False, auto_adjust=False)
        if df is None or len(df) < 5:
            df = yf.download(yf_sym, period="max", interval="1d", progress=False, auto_adjust=False)
        if df is not None and len(df) > 5:
            hi = float(df["High"].max()); lo = float(df["Low"].min())
            cur = float(df["Close"].dropna().iloc[-1])
            if hi and lo and hi > lo:
                return {
                    "high52": round(hi, 2), "low52": round(lo, 2), "latest": round(cur, 2),
                    "pct_from_high": round((cur - hi) / hi * 100, 2),
                    "pct_from_low": round((cur - lo) / lo * 100, 2),
                    "pos": round((cur - lo) / (hi - lo) * 100, 1),
                    "bars": len(df), "src": "yf",
                }
        return None
    except Exception as e:
        print(f"ERROR: _yf_history_52w {sym} {str(e).splitlines()[0]}")
        return None

_WATCH_KW_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "watch_52w.json")

def tv4002_history_52w(symbol, budget=20.0):
    """从 4002 /history 拉 52 周日线, 返回 {high52, low52, cur, pct_from_high, pct_from_low, pos}
    宽容模式: 有 >=1 段数据即返回 (部分 symbol 如 FX/CL 个别段 no_data, 不整体放弃)。
    budget: 总时间预算秒 (默认20s, 超时放弃 — 4002 某些 symbol 逐段 8s 超时会拖死预热)。
    """
    import time as _t
    t0 = _t.time()
    try:
        now = int(datetime.now().timestamp())
        from_ts = now - 365 * 24 * 3600
        closes, highs, lows = [], [], []
        # 4002 每次最多 90 天, 拆 5 段
        step = 75 * 24 * 3600
        for i in range(5):
            if _t.time() - t0 > budget:
                break
            s = from_ts + i * step
            e = min(from_ts + (i + 1) * step, now)
            if s >= e:
                break
            url = f"{TV4002}/history?symbol={symbol}&resolution=1D&from={s}&to={e}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=6) as r:
                d = json.loads(r.read().decode())
            if d.get("s") != "ok":
                continue
            highs += list(d.get("h") or [])
            lows += list(d.get("l") or [])
            closes += list(d.get("c") or [])
        if not closes:
            return None
        hi, lo = max(highs), min(lows)
        cur = closes[-1]
        if not hi or not lo or hi == lo:
            return None
        return {
            "high52": hi, "low52": lo, "latest": cur,
            "pct_from_high": round((cur - hi) / hi * 100, 2),
            "pct_from_low": round((cur - lo) / lo * 100, 2),
            "pos": round((cur - lo) / (hi - lo) * 100, 1),  # 当前价在52周区间的位置%
            "bars": len(closes),
        }
    except Exception as e:
        print(f"ERROR: tv4002_history_52w {symbol} {str(e).splitlines()[0]}")
        return None

def index_key_levels(ym, name):
    """指数 52周关键价位。优先 yfinance(稳定完整), 4002 兜底。返回 None 或 dict"""
    try:
        import yfinance as yf
        yf_sym = _yf_symbol_for(ym, name)
        if yf_sym:
            df = yf.download(yf_sym, period="1y", interval="1d", progress=False, auto_adjust=False)
            if df is not None and len(df) > 30:
                hi = float(df["High"].max()); lo = float(df["Low"].min())
                cur = float(df["Close"].dropna().iloc[-1])
                if hi and lo and hi > lo:
                    return {
                        "high52": round(hi, 2), "low52": round(lo, 2), "latest": round(cur, 2),
                        "pct_from_high": round((cur - hi) / hi * 100, 2),
                        "pct_from_low": round((cur - lo) / lo * 100, 2),
                        "pos": round((cur - lo) / (hi - lo) * 100, 1),
                        "src": "yf",
                    }
        return tv4002_history_52w(ym)  # 兜底
    except Exception as e:
        print(f"ERROR: index_key_levels {ym} {str(e).splitlines()[0]}")
        return tv4002_history_52w(ym)

HKT = 8  # Asia/Hong_Kong 固定 UTC+8

# 市场名称映射 (与 3400 对齐, 只读参考)
MARKET_INFO = {
    "cn": {"name": "中国", "flag": "🇨🇳"},
    "us": {"name": "美国", "flag": "🇺🇸"},
    "hk": {"name": "香港", "flag": "🇭🇰"},
    "kr": {"name": "韩国", "flag": "🇰🇷"},
    "tw": {"name": "台湾", "flag": "🇹🇼"},
    "jp": {"name": "日本", "flag": "🇯🇵"},
    "in": {"name": "印度", "flag": "🇮🇳"},
    "ch": {"name": "瑞士", "flag": "🇨🇭"},
    "de": {"name": "德国", "flag": "🇩🇪"},
    "uk": {"name": "英国", "flag": "🇬🇧"},
    "au": {"name": "澳大利亚", "flag": "🇦🇺"},
    "fr": {"name": "法国", "flag": "🇫🇷"},
    "nl": {"name": "荷兰", "flag": "🇳🇱"},
}

EVENT_TYPE_NAMES = {
    "earnings": "财报",
    "economic": "经济数据",
    "cb_rate": "央行决议",
    "ipo": "新股上市",
    "secondary_offering": "增发/第二上市",
    "new_high": "创历史新高",
    "new_low": "创历史新低",
    "breakout": "突破",
    "market_cap": "市值突破",
    "ipo_break": "破发",
    "sentiment": "舆情",
}

SIGNAL_TYPE_NAMES = {
    "new_high": "创历史新高",
    "new_low": "创历史新低",
    "breakout": "突破",
    "market_cap": "市值突破",
    "ipo_break": "破发",
}


# ──────────────────────────────────────────── IPO/新股事件 API
@app.route("/api/event_map/ipo")
def api_ipo():
    """新股上市/增发事件 — 3402 放量联动用
    ?date=YYYY-MM-DD 指定日期 (默认今天)
    ?market=CN/HK/US 指定市场
    ?ticker=XXX 按个股搜索
    """
    day = request.args.get("date") or now_hkt().strftime("%Y-%m-%d")
    market = request.args.get("market", "").upper()
    ticker = request.args.get("ticker", "").strip()
    try:
        start, end = settlement_window(day)
    except Exception as e:
        return jsonify({"error": f"date 格式错误: {str(e).splitlines()[0]}"}), 400

    # 查询 ipo + secondary_offering 事件
    where = "event_time >= %s AND event_time < %s AND event_type IN ('ipo', 'secondary_offering')"
    params = [start, end]
    if market:
        where += " AND country = %s"
        params.append(market)
    if ticker:
        where += " AND ticker LIKE %s"
        params.append(f"%{ticker}%")
    
    rows = db.query(f"SELECT * FROM event_earnings WHERE {where} ORDER BY event_time", tuple(params))
    events = []
    for r in rows:
        events.append({
            "ticker": r.get("ticker"),
            "event_type": r.get("event_type"),
            "event_time": _iso(r.get("event_time")),
            "title": r.get("title"),
            "country": r.get("country"),
            "importance": r.get("importance"),
        })
    return jsonify({"date": day, "market": market, "events": events})


@app.route("/api/event_map/search_by_ticker")
def api_search_by_ticker():
    """按 ticker/名称 搜索近期事件 (3402 放量联动用)
    ?q=搜索关键词 (ticker 或名称)
    ?days=7 搜索范围天数 (默认7天)
    """
    q = request.args.get("q", "").strip()
    days = request.args.get("days", 7, type=int)
    if not q:
        return jsonify({"error": "q 参数必填"}), 400
    
    now = now_hkt()
    start = now - timedelta(days=days)
    
    # 搜索 event_earnings 表 (ticker + title 模糊匹配)
    where = "event_time >= %s AND (ticker LIKE %s OR title LIKE %s)"
    params = [start, f"%{q}%", f"%{q}%"]
    rows = db.query(f"SELECT * FROM event_earnings WHERE {where} ORDER BY event_time DESC LIMIT 50", tuple(params))
    events = []
    for r in rows:
        events.append({
            "ticker": r.get("ticker"),
            "event_type": r.get("event_type"),
            "event_time": _iso(r.get("event_time")),
            "title": r.get("title"),
            "country": r.get("country"),
            "importance": r.get("importance"),
            "expected": r.get("expected"),
            "actual": r.get("actual"),
        })
    return jsonify({"query": q, "days": days, "events": events})


def now_hkt():
    """当前 HKT 时间 (naive, 数据库时间口径一致)。"""
    return datetime.utcnow() + timedelta(hours=HKT)


def settlement_window(day):
    """交割日换算: 输入 date (YYYY-MM-DD) -> (start, end)
    交割日口径: HKT 06:00 -> 次日 06:00。
    day 为 'YYYY-MM-DD' 字符串, 返回该日 06:00 起的 24 小时窗口。"""
    if isinstance(day, str):
        d = datetime.strptime(day, "%Y-%m-%d").date()
    else:
        d = day
    start = datetime.combine(d, time(6, 0, 0))
    end = datetime.combine(d + timedelta(days=1), time(6, 0, 0))
    return start, end


def _iso(dt):
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return str(dt)


def _float(v):
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


# ──────────────────────────────────────────── 1. 健康检查
@app.route("/api/event_map/health")
def api_health():
    counts = db.table_counts()
    return jsonify({
        "status": "ok",
        "service": "event-map-api",
        "port": 3401,
        "time_hkt": _iso(now_hkt()),
        "tables": counts,
    })


# ──────────────────────────────────────────── 2. 时间轴 (交割日全部事件)
@app.route("/api/event_map/timeline")
def api_timeline():
    day = request.args.get("date") or now_hkt().strftime("%Y-%m-%d")
    ftype = request.args.get("type")  # 可选: 只返回某类型, 加速加载
    try:
        start, end = settlement_window(day)
    except Exception as e:
        return jsonify({"error": f"date 格式错误, 应为 YYYY-MM-DD: {str(e).splitlines()[0]}"}), 400

    events = []

    # ── 批量 liquidity 映射 (避免逐条查 stock_liquidity, 提速)
    def _liq_map(tickers):
        tset = set(t for t in tickers if t)
        if not tset:
            return {}
        ph = ",".join(["%s"] * len(tset))
        rows = db.query(
            f"SELECT tv_ticker, liquidity_tier FROM stock_liquidity WHERE tv_ticker IN ({ph})",
            tuple(tset),
        )
        return {r["tv_ticker"]: r["liquidity_tier"] for r in rows}

    # event_earnings (财报/经济/央行)
    if not ftype or ftype in ("economic", "cb_rate"):
        e_rows = db.query(
            "SELECT ticker, event_type, event_time, title, country, expected, actual, importance, source "
            "FROM event_earnings WHERE event_time >= %s AND event_time < %s ORDER BY event_time",
            (start, end),
        )
        for r in e_rows:
            events.append({
                "type": r["event_type"],
                "type_name": EVENT_TYPE_NAMES.get(r["event_type"], r["event_type"]),
                "time": _iso(r["event_time"]),
                "title": r["title"],
                "ticker": r["ticker"],
                "market": r["country"],
                "asset_class": "经济/央行" if r["event_type"] in ("economic", "cb_rate") else "股票",
                "expected": r["expected"],
                "actual": r["actual"],
                "importance": r["importance"],
                "liquidity_tier": None,
                "source": r["source"],
            })

    # event_signals (行情信号: 新高/新低/突破等)
    sig_types = ("new_high", "new_low", "breakout", "volume_surge")
    if not ftype or ftype in sig_types:
        s_rows = db.query(
            "SELECT ticker, signal_type, signal_date, price, volume_ratio, consecutive_days, params_json, source "
            "FROM event_signals WHERE signal_date >= %s AND signal_date < %s ORDER BY signal_date, ticker",
            (start.date(), end.date()),
        )
        if s_rows:
            liqmap = _liq_map([r["ticker"] for r in s_rows])
        else:
            liqmap = {}
        for r in s_rows:
            # signal 是盘中信号, 归属 signal_date 所在交割日窗口 [signal_date 06:00, +1天 06:00)
            # 时刻定为当天 15:00 (各主要市场收盘后确认), 保证落在交割日窗口内
            sig_time = datetime.combine(_as_date(r["signal_date"]), time(15, 0))
            events.append({
                "type": r["signal_type"],
                "type_name": SIGNAL_TYPE_NAMES.get(r["signal_type"], r["signal_type"]),
                "time": _iso(sig_time),
                "title": f"{r['ticker']} {SIGNAL_TYPE_NAMES.get(r['signal_type'], r['signal_type'])}",
                "ticker": r["ticker"],
                "market": _market_of_ticker(r["ticker"]),
                "asset_class": "股票",
                "price": _float(r["price"]),
                "volume_ratio": _float(r["volume_ratio"]),
                "consecutive_days": r["consecutive_days"],
                "liquidity_tier": liqmap.get(r["ticker"]),
                "params": r["params_json"],
                "source": r["source"],
            })

    # event_sentiment (舆情)
    if not ftype or ftype == "sentiment":
        m_rows = db.query(
            "SELECT ticker, source, title, summary, sentiment, url, detected_at "
            "FROM event_sentiment WHERE detected_at >= %s AND detected_at < %s ORDER BY detected_at",
            (start, end),
        )
        if m_rows:
            sliqmap = _liq_map([r["ticker"] for r in m_rows])
        else:
            sliqmap = {}
        for r in m_rows:
            events.append({
                "type": "sentiment",
                "type_name": "舆情",
                "time": _iso(r["detected_at"]),
                "title": r["title"],
                "ticker": r["ticker"],
                "market": _market_of_ticker(r["ticker"]),
                "asset_class": "舆情",
                "sentiment": r["sentiment"],
                "summary": r["summary"],
                "url": r["url"],
                "liquidity_tier": sliqmap.get(r["ticker"]),
                "source": r["source"],
            })

    events.sort(key=lambda x: x["time"] or "")
    return jsonify({
        "date": day,
        "window_start": _iso(start),
        "window_end": _iso(end),
        "count": len(events),
        "events": events,
    })


# ──────────────────────────────────────────── 3. 财报列表 (未来7天, 流动性过滤)
@app.route("/api/event_map/earnings")
def api_earnings():
    tier = request.args.get("tier")
    day = request.args.get("date") or now_hkt().strftime("%Y-%m-%d")
    try:
        start, _ = settlement_window(day)
    except Exception as e:
        return jsonify({"error": f"date 格式错误: {str(e).splitlines()[0]}"}), 400
    end = start + timedelta(days=7)

    base = (
        "SELECT e.ticker, e.event_type, e.event_time, e.title, e.country, e.expected, e.actual, "
        "       e.importance, e.source, "
        "       (SELECT l.liquidity_tier FROM stock_liquidity l "
        "         WHERE l.tv_ticker = e.ticker COLLATE utf8mb4_general_ci "
        "            OR RIGHT(l.tv_ticker, CHAR_LENGTH(e.ticker)) = e.ticker COLLATE utf8mb4_general_ci "
        "         LIMIT 1) AS liquidity_tier "
        "FROM event_earnings e "
        "WHERE e.event_type = 'earnings' AND e.event_time >= %s AND e.event_time < %s"
    )
    params = [start, end]
    if tier:
        # 支持 L1/L2/... 或逗号分隔
        tiers = [t.strip().upper() for t in tier.split(",") if t.strip()]
        if tiers:
            placeholders = ",".join(["%s"] * len(tiers))
            sql = ("SELECT * FROM (" + base + ") t WHERE t.liquidity_tier IN (" + placeholders + ") "
                   "ORDER BY t.event_time")
            params.extend(tiers)
        else:
            sql = base + " ORDER BY e.event_time"
    else:
        sql = base + " ORDER BY e.event_time"

    rows = db.query(sql, tuple(params))
    result = []
    for r in rows:
        result.append({
            "ticker": r["ticker"],
            "event_type": r["event_type"],
            "time": _iso(r["event_time"]),
            "title": r["title"],
            "country": r["country"],
            "expected": r["expected"],
            "actual": r["actual"],
            "importance": r["importance"],
            "liquidity_tier": r["liquidity_tier"],
            "source": r["source"],
        })
    return jsonify({
        "date": day,
        "window_start": _iso(start),
        "window_end": _iso(end),
        "tier_filter": tier,
        "count": len(result),
        "earnings": result,
    })


# ──────────────────────────────────────────── 4. 经济数据/央行日历
@app.route("/api/event_map/economic")
def api_economic():
    day = request.args.get("date") or now_hkt().strftime("%Y-%m-%d")
    try:
        start, end = settlement_window(day)
    except Exception as e:
        return jsonify({"error": f"date 格式错误: {str(e).splitlines()[0]}"}), 400

    rows = db.query(
        "SELECT ticker, event_type, event_time, title, country, expected, actual, importance, source "
        "FROM event_earnings WHERE event_type IN ('economic','cb_rate') "
        "AND event_time >= %s AND event_time < %s ORDER BY event_time",
        (start, end),
    )
    result = []
    for r in rows:
        result.append({
            "ticker": r["ticker"],
            "event_type": r["event_type"],
            "type_name": EVENT_TYPE_NAMES.get(r["event_type"], r["event_type"]),
            "time": _iso(r["event_time"]),
            "title": r["title"],
            "country": r["country"],
            "expected": r["expected"],
            "actual": r["actual"],
            "importance": r["importance"],
            "source": r["source"],
        })
    return jsonify({
        "date": day,
        "window_start": _iso(start),
        "window_end": _iso(end),
        "count": len(result),
        "economic": result,
    })


# ──────────────────────────────────────────── 5. 资产状态
@app.route("/api/event_map/assets")
def api_assets():
    """资产状态: 指数/商品/加密/汇率 — 从 4002 实时行情 + MySQL 兜底"""
    from datetime import timezone
    # ── 4002 标的映射 (John 需求清单) ──
    index_map = [  # (4002symbol, 显示名, 市场)
        ("SPTRD", "标普500", "美国"), ("NASD", "纳斯达克", "美国"),
        ("DOW", "道琼斯", "美国"), ("RUSSELL", "罗素2000", "美国"),
        ("NIKKEI", "日经225", "日本"), ("TOPIX", "东证", "日本"),
        ("HSI", "恒生", "香港"), ("HHI", "恒生国企", "香港"), ("HTI", "恒生科技", "香港"),
        ("TAIWAN", "台湾加权", "台湾"), ("KOSPI", "韩国综合", "韩国"),
        ("ASX", "澳洲200", "澳洲"), ("SENSEX", "印度SENSEX", "印度"),
        ("FTSE", "英国100", "英国"), ("DAX", "德国40", "德国"),
        ("CAC", "法国40", "法国"), ("SMI", "瑞士20", "瑞士"),
        ("AEX", "荷兰AEX", "荷兰"), ("CN50", "中国A50", "中国"),
        ("STXE", "欧洲50", "欧洲"), ("IBEX", "西班牙35", "西班牙"), ("IT40", "意大利MIB", "意大利"),
    ]
    # 商品 (4002 CFD_COMMODITY 主符号)
    commodity_map = [
        ("CFDGOLD", "黄金", "贵金属"), ("CFDSILVER", "白银", "贵金属"),
        ("XPTUSD", "铂金", "贵金属"), ("XPDUSD", "钯金", "贵金属"),
        ("COPPER", "铜", "基本金属"), ("ALUMINIUM", "铝", "基本金属"),
        ("NICKEL", "镍", "基本金属"), ("ZINC", "锌", "基本金属"),
        ("LEAD", "铅", "基本金属"), ("TIN", "锡", "基本金属"),
        ("CL", "WTI原油", "能源"), ("LCO", "布伦特原油", "能源"),
        ("NG", "天然气", "能源"), ("RB", "汽油", "能源"),
        ("WHEAT", "小麦", "农产品"), ("C", "玉米", "农产品"),
        ("S", "大豆", "农产品"), ("COTTON", "棉花", "农产品"),
        ("SBO", "豆油", "农产品"), ("SM", "豆粕", "农产品"),
        ("HG", "COMEX铜", "基本金属"),
    ]
    # 加密
    crypto_map = [("BTC_SPOT", "BTC"), ("ETH_SPOT", "ETH"), ("SOL_SPOT", "SOL")]
    # 汇率 (兑美元, John 要求8对)
    fx_map = [
        ("JPY_FX", "USDJPY"), ("CNH_FX", "USDCNH"), ("CHF_FX", "USDCHF"),
        ("EUR_FX", "EURUSD"), ("GBP_FX", "GBPUSD"), ("CAD_FX", "USDCAD"),
        ("AUD_FX", "AUDUSD"), ("NZD_FX", "NZDUSD"), ("USDX_FX", "美元指数"),
    ]

    all_syms = [s for s, _, _ in index_map] + [s for s, _, _ in commodity_map] \
        + [s for s, _ in crypto_map] + [s for s, _ in fx_map]
    q = tv4002_quotes(all_syms)

    def fmt(sym, name, cls, grp, currency=""):
        v = q.get(sym, {})
        lp = v.get("lp")
        chp = v.get("chp")
        if lp is not None:
            return {"symbol": sym, "name": name, "asset_class": cls, "group": grp,
                    "currency": currency, "latest_value": float(lp),
                    "change_pct": chp, "status": "ok"}
        return {"symbol": sym, "name": name, "asset_class": cls, "group": grp,
                "currency": currency, "latest_value": None, "change_pct": None,
                "status": "no_quote", "note": "4002 无实时报价"}

    indexes = [fmt(s, n, "指数", m) for s, n, m in index_map]
    # 关键价位 (52周高/低): 并发拉每个指数历史, 内存缓存5分钟
    from time import time as _time
    from concurrent.futures import ThreadPoolExecutor, as_completed
    _cache = getattr(api_assets, "_kw_cache", None)
    if _cache is None or _time() - _cache["ts"] > 300:
        _cache = {"ts": _time(), "data": {}}
        setattr(api_assets, "_kw_cache", _cache)
    ok_syms = [idx["symbol"] for idx in indexes if idx["status"] == "ok"]
    todo = [s for s in ok_syms if s not in _cache["data"]]
    if todo:
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(index_key_levels, s, ""): s for s in todo}
            for f in as_completed(futs):
                try:
                    _cache["data"][futs[f]] = f.result() or {}
                except Exception:
                    _cache["data"][futs[f]] = {}
    for idx in indexes:
        kw = _cache["data"].get(idx["symbol"])
        if kw:
            idx["high52"] = kw.get("high52")
            idx["low52"] = kw.get("low52")
            idx["pct_from_high"] = kw.get("pct_from_high")
            idx["pct_from_low"] = kw.get("pct_from_low")
            idx["pos"] = kw.get("pos")
    commodities = [fmt(s, n, "商品", g) for s, n, g in commodity_map]
    crypto = [fmt(s, n, "加密", "加密") for s, n in crypto_map]
    forex = [fmt(s, n, "汇率", "外汇") for s, n in fx_map]

    return jsonify({
        "time_hkt": _iso(now_hkt()),
        "source": "4002+mysql",
        "summary": {"indexes": len(indexes), "commodities": len(commodities),
                    "crypto": len(crypto), "forex": len(forex)},
        "indexes": indexes, "commodities": commodities, "crypto": crypto, "forex": forex,
    })


# ──────────────────────────────────────────── 6. 市场交易状态
@app.route("/api/event_map/market_status")
def api_market_status():
    now = now_hkt()
    sessions = db.query(
        "SELECT market_code, session_type, open_time, close_time, crosses_midnight "
        "FROM market_sessions ORDER BY market_code, open_time"
    )
    now_min = now.hour * 60 + now.minute

    def parse_time(td):
        total = int(_parse_dur(td).total_seconds()) // 60
        return total

    markets = {}
    for s in sessions:
        mc = s["market_code"]
        open_min = parse_time(s["open_time"])
        close_min = parse_time(s["close_time"])
        cr_mid = s["crosses_midnight"]
        stype = s["session_type"]

        # 每个市场可能有多个 session (午休/夏令冬令), 只要任一处于 session 即视为 session
        status = _session_status(open_min, close_min, cr_mid, now_min)
        entry = markets.setdefault(mc, {
            "market_code": mc,
            "name": MARKET_INFO.get(mc.lower(), {}).get("name", mc),
            "flag": MARKET_INFO.get(mc.lower(), {}).get("flag", ""),
            "status": "closed",
            "sessions": [],
        })
        entry["sessions"].append({
            "session_type": stype,
            "open": _td_str(s["open_time"]),
            "close": _td_str(s["close_time"]),
            "crosses_midnight": bool(cr_mid),
            "status": status,
        })
        # 聚合: 任一 session 则提升整体状态
        if status == "session":
            entry["status"] = "session"
        elif status in ("pre_market", "after_market") and entry["status"] == "closed":
            entry["status"] = status

    return jsonify({
        "time_hkt": _iso(now),
        "markets": markets,
    })


def _session_status(open_min, close_min, cr_mid, now_min):
    """返回单个 session 的状态: session / pre_market / after_market / closed"""
    if cr_mid or close_min < open_min:
        # 跨午夜 (如美股 21:30 -> 04:00+1)
        adjusted_now = now_min + 1440 if now_min < open_min else now_min
        adjusted_close = close_min + (1440 if close_min < open_min else 0)
        if open_min <= adjusted_now < adjusted_close:
            return "session"
        if adjusted_now < open_min:
            return "pre_market"
        return "after_market"
    else:
        if open_min <= now_min < close_min:
            return "session"
        if now_min < open_min:
            return "pre_market"
        return "after_market"


def _td_str(td):
    total = int(_parse_dur(td).total_seconds())
    h = total // 3600
    m = (total % 3600) // 60
    return f"{h:02d}:{m:02d}"


# ──────────────────────────────────────────── 7. 富途式列表日历 (更新 v2, John 2026-08-14)
@app.route("/api/event_map/calendar")
def api_calendar():
    """列表式交易日历。交割日 06:00 切割 (HKT)。
    返回: 盘前重点区 (盘后财报/明日盘前财报/重点经济) + 当日全部事件(带级别) + 开盘时段重点价位。
    """
    day = request.args.get("date") or now_hkt().strftime("%Y-%m-%d")
    try:
        start, end = settlement_window(day)
    except Exception as e:
        return jsonify({"error": f"date 格式错误: {str(e).splitlines()[0]}"}), 400
    nxt_start, nxt_end = settlement_window((datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d"))
    day_d = datetime.strptime(day, "%Y-%m-%d").date()

    events = []

    # ① 财报/经济/央行 (全部, 带级别)
    e_rows = db.query(
        "SELECT ticker, event_type, event_time, title, country, expected, actual, importance, source "
        "FROM event_earnings WHERE event_time >= %s AND event_time < %s ORDER BY event_time",
        (start, end),
    )
    e_tickers = [r["ticker"] for r in e_rows if r["event_type"] == "earnings"]
    e_liq = _liq_map(e_tickers)
    for r in e_rows:
        is_earn = r["event_type"] == "earnings"
        events.append({
            "type": r["event_type"], "type_name": EVENT_TYPE_NAMES.get(r["event_type"], r["event_type"]),
            "time": _iso(r["event_time"]), "title": r["title"], "ticker": r["ticker"],
            "market": r["country"], "asset_class": "经济/央行" if r["event_type"] in ("economic", "cb_rate") else "股票",
            "expected": r["expected"], "actual": r["actual"], "importance": r["importance"],
            "liquidity_tier": e_liq.get(r["ticker"]) if is_earn else None,
            "source": r["source"],
        })

    # ② 行情信号 (新高/新低/突破) — 属于 signal_date 所在交割日
    sig_types = ("new_high", "new_low", "breakout")
    s_rows = db.query(
        "SELECT ticker, signal_type, signal_date, price, volume_ratio, consecutive_days, params_json, source "
        "FROM event_signals WHERE signal_date >= %s AND signal_date < %s ORDER BY signal_date, ticker",
        (start.date(), end.date()),
    )
    s_liq = _liq_map([r["ticker"] for r in s_rows]) if s_rows else {}
    for r in s_rows:
        # signal 时刻定为 15:00 (收盘后确认), 落在交割日窗口
        sig_time = datetime.combine(_as_date(r["signal_date"]), time(15, 0))
        events.append({
            "type": r["signal_type"], "type_name": SIGNAL_TYPE_NAMES.get(r["signal_type"], r["signal_type"]),
            "time": _iso(sig_time), "title": f"{r['ticker']} {SIGNAL_TYPE_NAMES.get(r['signal_type'], r['signal_type'])}",
            "ticker": r["ticker"], "market": _market_of_ticker(r["ticker"]),
            "asset_class": "股票", "price": _float(r["price"]),
            "volume_ratio": _float(r["volume_ratio"]), "consecutive_days": r["consecutive_days"],
            "liquidity_tier": s_liq.get(r["ticker"]), "source": r["source"],
        })

    events.sort(key=lambda x: x["time"] or "")

    # ── 盘前重点区 ──
    # 今日盘前财报: 今日窗口内 15:00-22:00 (美股盘前财报 ET 08:00 = HKT 20:00, 跨日/亚洲财报另归)
    # 修复(2026-08-26): 原 06:00-15:00 把美股盘前财报(20:00)漏掉, 导致今日盘前永远为空
    today_pre = [e for e in events if e["type"] == "earnings" and e["time"] and "15:00" <= e["time"][11:16] < "22:00"]
    # 盘后财报: 今日窗口内 >=22:00 或 <06:00 (美股盘后跨日 04:00~06:00 HKT)
    # 典型: NVDA 财报 08-27 04:00 HKT (美股盘后 16:00 ET, 跨日到 HKT 凌晨)
    after_hours = [e for e in events if e["type"] == "earnings" and e["time"] and (
        e["time"][11:13] >= "22" or e["time"][11:13] < "06"
    )]
    # 明日盘前财报: 次日窗口内 15:00-22:00 (美股明日盘前财报 HKT 20:00)
    pre_rows = db.query(
        "SELECT ticker, event_type, event_time, title, country, expected, actual, importance, source "
        "FROM event_earnings WHERE event_type='earnings' AND event_time >= %s AND event_time < %s "
        "AND HOUR(event_time) >= 15 AND HOUR(event_time) < 22 ORDER BY event_time",
        (nxt_start, nxt_end),
    )
    pre_liq = _liq_map([r["ticker"] for r in pre_rows]) if pre_rows else {}
    pre_earnings = [{
        "type": "earnings", "time": _iso(r["event_time"]), "title": r["title"], "ticker": r["ticker"],
        "country": r["country"], "expected": r["expected"], "actual": r["actual"],
        "importance": r["importance"], "liquidity_tier": pre_liq.get(r["ticker"]),
    } for r in pre_rows]
    # 重点经济数据: 未来 24h 重要性 >=2 的经济/央行事件
    eco_rows = db.query(
        "SELECT ticker, event_type, event_time, title, country, expected, actual, importance, source "
        "FROM event_earnings WHERE event_type IN ('economic','cb_rate') "
        "AND event_time >= %s AND event_time < %s AND importance >= 2 ORDER BY event_time",
        (start, nxt_end),
    )
    key_economic = [{
        "type": r["event_type"], "time": _iso(r["event_time"]), "title": r["title"],
        "country": r["country"], "expected": r["expected"], "actual": r["actual"],
        "importance": r["importance"], "ticker": r["ticker"],
    } for r in eco_rows]

    # ── 异常放量列: 3402放量板块 × 3404持续放量 × SECTOR_LINK_MAP ──
    # 显示: 市场 → 异常放量板块 → 对应持续放量商品[板块] → 持续放量时间
    # 经模块级缓存 (TTL 5min), 避免上游 3402(~40s) 拖慢每次 calendar 请求
    surge_sectors = _get_surge_sectors()

    # ── 开盘时段重点价位 (各市场开盘时刻, L1 信号聚合) ──
    # 用 market_sessions 拿今日各市场开盘时刻, 聚合同市场 L1 信号
    sessions = db.query(
        "SELECT market_code, open_time FROM market_sessions "
        "WHERE session_type IN ('session','session_summer','session_winter','day','day_session') "
        "ORDER BY market_code, open_time"
    )
    from collections import defaultdict
    signals_by_market = defaultdict(list)
    for e in events:
        if e["type"] in sig_types and e["liquidity_tier"] == "L1":
            m = (e.get("market") or "").lower()
            signals_by_market[m].append(e)
    # 市场代码映射到 country 关键词
    mc2kw = {"us": "美", "cn": "中", "hk": "港", "jp": "日", "kr": "韩", "tw": "台",
             "au": "澳", "in": "印", "de": "德", "uk": "英", "fr": "法", "nl": "荷", "ch": "瑞"}
    open_slots = []
    seen_markets = set()
    for s in sessions:
        mc_raw = s["market_code"]
        mc = str(mc_raw).lower()  # market_sessions 是大写 (AU/CN/HK), signals market 是小写
        if mc in seen_markets:
            continue
        seen_markets.add(mc)
        kw = mc2kw.get(mc, mc)
        # 匹配: market 可能是代码 (hk/us) 或中文 (香港/美国), 双轨匹配 (大小写不敏感)
        sigs = [e for e in events if e["type"] in sig_types and (
            str(e.get("market") or "").lower() == mc
            or kw in str(e.get("market") or "")
        )]
        if not sigs:
            continue
        open_t = s["open_time"]
        total = int(_parse_dur(open_t).total_seconds())
        hh, mm = total // 3600, (total % 3600) // 60
        # 若开盘在凌晨 (US 21:30 跨日), 归一为当日时间
        disp = f"{hh:02d}:{mm:02d}"
        open_slots.append({
            "market": mc, "market_name": MARKET_INFO.get(mc, {}).get("name", mc),
            "flag": MARKET_INFO.get(mc, {}).get("flag", ""), "open_time": disp,
            "counts": {
                "new_high": sum(1 for e in sigs if e["type"] == "new_high"),
                "new_low": sum(1 for e in sigs if e["type"] == "new_low"),
                "breakout": sum(1 for e in sigs if e["type"] == "breakout"),
            },
            "top": sorted(sigs, key=lambda e: -(e.get("consecutive_days") or 0))[:5],
        })

    return jsonify({
        "date": day, "window_start": _iso(start), "window_end": _iso(end),
        "count": len(events), "events": events,
        "pre_market": {"today_pre_earnings": today_pre, "after_hours_earnings": after_hours,
                       "next_pre_earnings": pre_earnings, "key_economic": key_economic,
                       "surge_sectors": surge_sectors},
        "open_slots": open_slots,
    })


# ───────────────── 7.5 每日延迟回顾 (Daily Review, John 2026-09-02)
_DAILY_REVIEW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "daily_review")
_DAILY_REVIEW_COLUMN_ORDER = {
    "us": {"flag": "🇺🇸", "name": "US", "top": 20},
    "cn": {"flag": "🇨🇳", "name": "CN", "top": 20},
    "hk": {"flag": "🇭🇰", "name": "HK", "top": 20},
    "jp": {"flag": "🇯🇵", "name": "JP", "top": 20},
    "uk": {"flag": "🇬🇧", "name": "UK", "top": 20},
    "fr": {"flag": "🇫🇷", "name": "FR", "top": 15},
    "de": {"flag": "🇩🇪", "name": "DE", "top": 15},
}
def _daily_review_path(dstr):
    return os.path.join(_DAILY_REVIEW_DIR, f"{dstr}.json")


def _daily_review_available_dates():
    """返回 data/daily_review 下所有快照日期(倒序)。"""
    if not os.path.isdir(_DAILY_REVIEW_DIR):
        return []
    out = []
    for fn in os.listdir(_DAILY_REVIEW_DIR):
        if fn.endswith(".json"):
            out.append(fn[:-5])
    return sorted(out, reverse=True)


@app.route("/api/event_map/daily_review")
def api_daily_review():
    """每日延迟回顾快照。?date=YYYY-MM-DD(默认最新)。返回快照+可用日期列表。"""
    default_date = _daily_review_available_dates()[0] if _daily_review_available_dates() else datetime.now().strftime("%Y-%m-%d")
    dstr = request.args.get("date", default_date)
    if not os.path.exists(_daily_review_path(dstr)):
        return jsonify({"error": f"快照 {dstr} 不存在", "available": _daily_review_available_dates(), "date": dstr}), 404
    with open(_daily_review_path(dstr), "r", encoding="utf-8") as f:
        data = json.load(f)
    return jsonify({
        "available": _daily_review_available_dates(),
        "columns": _DAILY_REVIEW_COLUMN_ORDER,
        **data,
    })


# ──────────────────────────────────────────── 8. 实时监控 (逼近突破位, John 2026-08-14)
_WATCH_KW_CACHE = {"ts": 0, "data": {}, "missing": [], "retry_ts": 0}  # symbol -> 52周高低, 10分钟缓存
_WATCH_PREPARING = False  # 预热中标志: 请求期间不碰 4002 52周拉取, 避免并发全挂


def _save_watch_cache():
    """将当前 52周缓存写入磁盘 (下次重启秒载)"""
    try:
        import json
        data = _WATCH_KW_CACHE.get("data", {})
        missing = _WATCH_KW_CACHE.get("missing", [])
        os.makedirs(os.path.dirname(_WATCH_KW_FILE), exist_ok=True)
        with open(_WATCH_KW_FILE, "w", encoding="utf-8") as f:
            json.dump({"data": data, "missing": sorted(missing)}, f, ensure_ascii=False)
    except Exception as e:
        print(f"WARN: save_watch_cache {str(e).splitlines()[0]}")

@app.route("/api/event_map/watch")
def api_watch():
    """实时监控: 股票(3403 swap-klines) + 指数/期货/商品(4002 quotes) 逼近 52周突破位的标的。
    触发条件: 现价进入 52周区间顶部/底部 8% 以内 (pos>=92 或 pos<=8)。
    """
    from time import time as _t
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _fetch(url, timeout=8):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())

    out = {"time_hkt": _iso(now_hkt()), "stocks": [], "indices": [], "commodities": [], "forex": [], "bonds": [], "crypto": [], "no_quote": []}

    # ① 股票: 3403 swap-klines (248只实时)
    try:
        d = _fetch(f"{config.SRV_3403}/api/swap-klines")
        stocks = d.get("stocks") or []
        if stocks:
            tickers = [s["stock_ticker"] for s in stocks if s.get("stock_ticker")]
            ph = ",".join(["%s"] * len(tickers))
            # 52周高低 (ohlcv 批量, 一年窗口)
            rows = db.query(
                f"SELECT tv_ticker, MAX(high) h52, MIN(low) l52 FROM ohlcv "
                f"WHERE date >= DATE_SUB(CURDATE(), INTERVAL 365 DAY) AND tv_ticker IN ({ph}) "
                f"GROUP BY tv_ticker",
                tuple(tickers),
            )
            kw = {r["tv_ticker"]: (float(r["h52"]), float(r["l52"])) for r in rows}
            # 级别映射
            liq = _liq_map(tickers)
            for s in stocks:
                tk = s.get("stock_ticker")
                if not tk or tk not in kw:
                    continue
                # ⚠️ 币种校验: 港股 swap 是 USD 计价, ohlcv 是 HKD — 单位错配会导致 pos 失真。
                #    只监控美股 (NASDAQ:/NYSE:/AMEX:) 且 swap 价与 ohlcv 最新收盘差异 <30% 的标的。
                if not (tk.startswith(("NASDAQ:", "NYSE:", "AMEX:", "ARCA:", "BATS:"))):
                    continue
                h52, l52 = kw[tk]
                price = s.get("price")
                if not price or not h52 or not l52 or h52 <= l52:
                    continue
                pos = (price - l52) / (h52 - l52) * 100
                # 逼近突破位: 顶部 8% 或底部 8% (clamp 到 [0,100] 防越界)
                pos = max(0.0, min(100.0, pos))
                near = pos >= 92 or pos <= 8
                if not near:
                    continue
                direction = "up" if pos >= 92 else "down"
                out["stocks"].append({
                    "ticker": tk, "name": s.get("name") or tk, "market": s.get("market_cn") or s.get("market") or "",
                    "price": round(price, 4), "pct": s.get("pct"), "high52": round(h52, 4),
                    "low52": round(l52, 4), "pos": round(pos, 1), "direction": direction,
                    "tier": liq.get(tk), "source": s.get("source"),
                })
    except Exception as e:
        print(f"ERROR: watch.stocks {str(e).splitlines()[0]}")

    # ② 指数/商品/汇率/债券/CRYPTO: 4002 quotes + 52周 (并发 + 10分钟缓存)
    # ⚠️ 52周统一用 4002 /history (与 quotes 同源), 不用 yfinance —
    #     yf 的 ^TWII(台湾)/000300.SS(A50) 与 4002 报价标的不同, 曾导致 pos=1192% 失真。
    index_map = [
        ("SPTRD", "标普500"), ("NASDAQ", "纳斯达克"), ("DOW", "道琼斯"), ("RUSSELL", "罗素2000"),
        ("NIKKEI", "日经225"), ("HSI", "恒生"), ("HHI", "恒生国企"), ("HTI", "恒生科技"),
        ("TAIWAN", "台湾加权"), ("KOSPI", "韩国综合"), ("ASX", "澳洲200"), ("SENSEX", "印度SENSEX"),
        ("FTSE", "英国100"), ("DAX", "德国40"), ("CAC", "法国40"), ("CN50", "中国A50"),
        ("HK50", "恒生HS50"),
    ]
    commodity_map = [
        ("CFDGOLD", "黄金"), ("CFDSILVER", "白银"), ("COPPER", "铜"), ("CL", "WTI原油"),
        ("LCO", "布伦特原油"), ("NG", "天然气"), ("RB", "汽油"), ("WHEAT", "小麦"),
        ("C", "玉米"), ("S", "大豆"), ("COTTON", "棉花"),
    ]
    fx_map = [("JPY_FX", "USDJPY"), ("CNH_FX", "USDCNH"), ("EUR_FX", "EURUSD"),
              ("GBP_FX", "GBPUSD"), ("USDX_FX", "美元指数")]
    bond_map = [("US_BOND", "美债"), ("EURO_BOND", "欧债"), ("CN_BOND", "中债"), ("JP_BOND", "日债")]
    crypto_map = [("BTC_SPOT", "BTC"), ("ETH_SPOT", "ETH"), ("SOL_SPOT", "SOL"),
                  ("BNB_SPOT", "BNB"), ("XRP_SPOT", "XRP"), ("DOGE_SPOT", "DOGE")]
    all_syms = [s for s, _ in index_map + commodity_map + fx_map + bond_map + crypto_map]

    # 52周数据缓存 (10分钟) — 失败项 (missing) 每5分钟补拉一次
    # ⚠️ 预热中 (启动后台线程正在拉 52周) 时请求完全不打 4002 (quotes+history), 避免并发全挂
    cache = _WATCH_KW_CACHE
    now_ts = _t()
    need_refresh = now_ts - cache.get("ts", 0) > 600
    need_retry = cache.get("missing") and now_ts - cache.get("retry_ts", 0) > 300
    if (need_refresh or need_retry) and not _WATCH_PREPARING:
        # 只对有实时报价的 symbol 拉 52 周 (跳过 no_quote, 减少无效请求)
        qq = tv4002_quotes(all_syms)
        live_syms = [s for s in all_syms if qq.get(s, {}).get("lp") is not None]
        missing = set(cache.get("missing") or [])
        todo = [s for s in live_syms if s not in cache.get("data", {}) or s in missing]
        if todo:
            def _load(sym):
                try:
                    v = tv4002_history_52w(sym, 25) or {}
                    # 4002 数据异常 (脏数据 >30x): 尝试 yfinance 兜底
                    if v and v.get("high52") and v.get("low52"):
                        lp = qq.get(sym, {}).get("lp")
                        if lp and lp > 0 and (v["high52"] > lp * 30 or v["low52"] * 30 < lp):
                            yf_v = _yf_history_52w(sym)
                            if yf_v:
                                v = yf_v
                    return v
                except Exception:
                    return {}
            with ThreadPoolExecutor(max_workers=3) as ex:
                futs = {ex.submit(_load, s): s for s in todo}
                data = dict(cache.get("data") or {})
                still_missing = set()
                for f in as_completed(futs):
                    try:
                        v = f.result()
                        if v:
                            data[futs[f]] = v
                        else:
                            still_missing.add(futs[f])
                    except Exception:
                        still_missing.add(futs[f])
            cache["data"] = data
            cache["missing"] = sorted(still_missing)
        cache["ts"] = now_ts
        cache["retry_ts"] = now_ts

    if _WATCH_PREPARING:
        # 预热中: 不打 4002, 指数/商品等返回"预热中"标注, 股票走 3403 (独立于 4002)
        out["preparing"] = True
        for sym, name in index_map + commodity_map + fx_map + bond_map + crypto_map:
            out["no_quote"].append({"symbol": sym, "name": name, "note": "52周数据预热中…"})
        out["summary"] = {k: len(v) for k, v in out.items() if isinstance(v, list)}
        return jsonify(out)

    q = tv4002_quotes(all_syms)

    def _watch_asset(sym, name, cls):
        v = q.get(sym, {})
        lp = v.get("lp")
        # 无行情: 记录到 no_quote (前端标注"暂时无行情"); 但先尝试 yfinance 兜底
        if lp is None:
            kw = _yf_history_52w(sym, name)
            if kw and kw.get("latest"):
                lp = kw["latest"]
                cache["data"][sym] = kw
                _save_watch_cache()
            else:
                out["no_quote"].append({"symbol": sym, "name": name, "cls": cls})
                return None
        kw = cache["data"].get(sym)
        if not kw or not kw.get("high52") or not kw.get("low52"):
            # 有行情但无 52 周数据: 尝试 yfinance 兜底
            kw = _yf_history_52w(sym, name)
            if kw:
                cache["data"][sym] = kw
                _save_watch_cache()
            else:
                out["no_quote"].append({"symbol": sym, "name": name, "cls": cls, "note": "无52周历史"})
                return None
        # ⚠️ 脏数据防护: 52周高/低与现价偏差 >30x 视为历史脏值 (如 CL high52=11574 vs 现价82)
        if lp > 0 and (kw["high52"] > lp * 30 or kw["low52"] * 30 < lp):
            # 尝试 yfinance 兜底 (4002 数据单位异常)
            yf_kw = _yf_history_52w(sym, name)
            if yf_kw:
                cache["data"][sym] = yf_kw
                kw = yf_kw
                _save_watch_cache()
            else:
                out["no_quote"].append({"symbol": sym, "name": name, "cls": cls, "note": "历史数据异常"})
                return None
        pos = (lp - kw["low52"]) / (kw["high52"] - kw["low52"]) * 100
        pos = max(0.0, min(100.0, pos))
        if not (pos >= 92 or pos <= 8):
            return None
        direction = "up" if pos >= 92 else "down"
        # 距突破位: up=距新高点数, down=距新低点数 (实时价与 52周极值差)
        dist = round(kw["high52"] - lp, 4) if direction == "up" else round(lp - kw["low52"], 4)
        return {"symbol": sym, "name": name, "price": round(float(lp), 4),
                "pct": v.get("chp"), "high52": kw.get("high52"), "low52": kw.get("low52"),
                "pos": round(pos, 1), "direction": direction, "dist": dist}

    for sym, name in index_map:
        a = _watch_asset(sym, name, "指数")
        if a:
            out["indices"].append(a)
    for sym, name in commodity_map:
        a = _watch_asset(sym, name, "商品")
        if a:
            out["commodities"].append(a)
    for sym, name in fx_map:
        a = _watch_asset(sym, name, "汇率")
        if a:
            out["forex"].append(a)
    for sym, name in bond_map:
        a = _watch_asset(sym, name, "债券")
        if a:
            out["bonds"].append(a)
    for sym, name in crypto_map:
        a = _watch_asset(sym, name, "加密")
        if a:
            out["crypto"].append(a)

    out["summary"] = {k: len(v) for k, v in out.items() if isinstance(v, list)}
    return jsonify(out)


def _liq_map(tickers):
    """批量 ticker -> liquidity_tier 映射 (stock_liquidity, 与 3400 一致)。
    美股裸码 (TE/FBRX) 自动扩展 NASDAQ:/NYSE:/AMEX: 前缀匹配 (库内带前缀)。
    """
    tset = set(t for t in tickers if t)
    if not tset:
        return {}
    expanded = set()
    for t in tset:
        expanded.add(t)
        if ":" not in t and not t.endswith((".US", ".HK", ".SS", ".SZ")):
            expanded.add(f"NASDAQ:{t}")
            expanded.add(f"NYSE:{t}")
            expanded.add(f"AMEX:{t}")
    ph = ",".join(["%s"] * len(expanded))
    rows = db.query(
        f"SELECT tv_ticker, liquidity_tier FROM stock_liquidity WHERE tv_ticker IN ({ph})",
        tuple(expanded),
    )
    m = {r["tv_ticker"]: r["liquidity_tier"] for r in rows}
    # 裸码查询: 优先取带前缀的匹配
    out = {}
    for t in tset:
        if t in m:
            out[t] = m[t]
        else:
            for pfx in ("NASDAQ:", "NYSE:", "AMEX:"):
                if pfx + t in m:
                    out[t] = m[pfx + t]
                    break
    return out


# ──────────────────────────────────────────── 辅助函数
def _market_of_ticker(ticker):
    """从 ticker 推断市场。格式: SSE:xxx / SZSE:xxx / xxx.US / xxx.HK 等"""
    t = (ticker or "").upper()
    if ":" in t:
        prefix = t.split(":", 1)[0]
        return {"SSE": "cn", "SZSE": "cn", "BSE": "in", "NSE": "in",
                "ASX": "au", "TSE": "jp", "KRX": "kr", "TWSE": "tw",
                "HKEX": "hk", "NASDAQ": "us", "NYSE": "us", "AMEX": "us",
                "ARCA": "us", "BATS": "us"}.get(prefix, prefix)
    if t.endswith(".US"):
        return "us"
    if t.endswith(".HK"):
        return "hk"
    if t.endswith(".SS"):
        return "cn"
    if t.endswith(".SZ"):
        return "cn"
    return None


def _tier_of(ticker):
    if not ticker:
        return None
    rows = db.query("SELECT liquidity_tier FROM stock_liquidity WHERE tv_ticker = %s LIMIT 1", (ticker,))
    return rows[0]["liquidity_tier"] if rows else None


def _index_market_name(symbol, market):
    """指数所属市场。优先 index_meta.market, 其次按 symbol 前缀推断。"""
    if market:
        return MARKET_INFO.get(str(market).lower(), {}).get("name", market)
    s = (symbol or "").upper()
    if s.startswith("^"):  # TradingView 指数
        return "国际指数"
    if s.endswith(".SS"):
        return "中国"
    return "国际指数"


# ──────────────────────────────────────────── 全球重大事件监控 (2026-09-17)
from global_events.classify import MEGA_CAPS as _GE_MEGA, load_static as _ge_load_static
from global_events.scoring import heat_scheduled as _ge_heat_scheduled, apply_decay as _ge_apply_decay
from global_events.health import load_health as _ge_load_health

_GE_TODAY_MAX = 40
_GE_BREAKING_MIN_HEAT = 20.0
# 用户要求 (2026-09-17): 只显示 4 星及以上, 且科技发布会/展会/未分类一律不显示 (采集照常, 只在展示层过滤)
_GE_MIN_IMPORTANCE = 4
_GE_EXCLUDED_CATEGORIES = {"tech_event", "other"}


def _ge_is_major(it):
    return int(it.get("importance") or 0) >= _GE_MIN_IMPORTANCE and it.get("category") not in _GE_EXCLUDED_CATEGORIES


# 🚨 突发播报 (用户要求 2026-09-17): 最近 24h 的突发快讯, 只要地缘/金融风险/能源/央行/中国政策类且 ≥3 星,
# 或标题命中"总统/战争"类关键词 (总统推文由见闻/财联社/Google News 转述), 按时间倒序。
_GE_BREAKING_CATEGORIES = {"geopolitics", "fin_risk", "energy_supply", "central_bank", "cn_policy"}
_GE_BREAKING_MIN_IMPORTANCE = 3
_GE_BREAKING_MAX = 30
# 硬关键词: 战争/关税/制裁/袭击 — 即使只有 2 星 (Google News 单篇) 也进
_GE_BREAKING_HARD = _re.compile(
    r"战争|开战|宣战|袭击|空袭|导弹|爆炸|停火|紧急状态|关税|制裁|封锁|军事行动|"
    r"invasion|airstrike|declares war|state of emergency|tariff|sanction|ceasefire|missile|blockade", _re.I)
# 人物关键词: 总统/领导人表态 — 需 ≥3 星 (多源同报/财联社 B 级以上) 才进, 避免"特朗普谈犯罪"这类无关报道
_GE_BREAKING_PERSON = _re.compile(
    r"特朗普|Trump|白宫|White House|Truth Social|美国总统|习近平|普京|Putin|Pentagon", _re.I)
_GE_BREAKING_DEDUP_SIM = 0.6


def _ge_is_breaking_news(it):
    title = it.get("title") or ""
    imp = int(it.get("importance") or 0)
    if _GE_BREAKING_HARD.search(title):
        return True
    if imp < _GE_BREAKING_MIN_IMPORTANCE:
        return False
    return it.get("category") in _GE_BREAKING_CATEGORIES or _GE_BREAKING_PERSON.search(title) is not None


def _ge_dedupe_breaking(items):
    """列表已按时间倒序; 相似标题 (difflib ≥ 0.6) 只保留最新一条。"""
    from global_events.merge import similarity as _sim
    kept = []
    for it in items:
        if any(_sim(it["title"], k["title"]) >= _GE_BREAKING_DEDUP_SIM for k in kept):
            continue
        kept.append(it)
    return kept
_GE_COLS = ("kind, category, title, summary, country, event_time, importance, heat_base, heat_score, "
            "reading_num, expected, previous, actual, tickers, source, url")


def _ge_dt(v):
    """db-pool 返回的 DATETIME 可能是 str ('YYYY-MM-DD HH:MM:SS' / ISO) 或 datetime。"""
    if isinstance(v, datetime):
        return v
    s = str(v).replace("T", " ")[:19]
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return datetime.strptime(s[:16], "%Y-%m-%d %H:%M")


def _ge_item(kind, category, title, event_time, importance, heat, **extra):
    d = {"kind": kind, "category": category, "title": title, "time": _iso(event_time),
         "importance": int(importance or 1), "heat": round(float(heat or 0), 1),
         "country": None, "source": None, "url": None, "tickers": None, "summary": None,
         "expected": None, "actual": None, "previous": None, "reading_num": None}
    d.update(extra)
    return d


def _ge_row(r, now):
    et = _ge_dt(r["event_time"])
    heat = float(r.get("heat_score") or 0)
    if r["kind"] == "breaking":
        heat = _ge_apply_decay(float(r.get("heat_base") or heat), et, now)
    return _ge_item(r["kind"], r["category"], r["title"], et, r["importance"], heat,
                    country=r.get("country"), source=r.get("source"), url=r.get("url"),
                    tickers=r.get("tickers"), summary=r.get("summary"), expected=r.get("expected"),
                    actual=r.get("actual"), previous=r.get("previous"), reading_num=r.get("reading_num"))


def _ge_settle_key(dt):
    """结算日分组键: HKT 06:00 换日, 与前端日历/交割日口径一致。"""
    return (dt - timedelta(hours=6)).strftime("%Y-%m-%d")


def _ge_mega_earnings(start, end):
    """现有 event_earnings 里的万亿市值巨头财报 → scheduled 事件。"""
    tickers = list(_GE_MEGA.keys())
    ph = ",".join(["%s"] * len(tickers))
    rows = db.query(
        f"SELECT ticker, event_time, title FROM event_earnings "
        f"WHERE event_type='earnings' AND ticker IN ({ph}) AND event_time >= %s AND event_time < %s "
        f"ORDER BY event_time",
        tuple(tickers) + (start, end))
    out = []
    for r in rows:
        tk = r["ticker"]
        name = _GE_MEGA.get(tk, {}).get("name", tk)
        ev = {"category": "earnings", "importance": 4, "country": "美国", "expected": None, "actual": None}
        out.append(_ge_item("scheduled", "earnings", f"{name} ({tk}) 财报", _ge_dt(r["event_time"]), 4,
                            _ge_heat_scheduled(ev), country="美国", source="earnings", tickers=tk))
    return out


def _ge_policy_events(start, end):
    """手工维护的中国政策会议 JSON → scheduled 事件。"""
    out = []
    for p in _ge_load_static("cn_policy_events.json"):
        try:
            et = datetime.strptime(f"{p['date']} {p.get('time', '09:00')}", "%Y-%m-%d %H:%M")
        except Exception:
            continue
        if not (start <= et < end):
            continue
        imp = int(p.get("importance", 3))
        ev = {"category": "cn_policy", "importance": imp, "country": "中国", "expected": None, "actual": None}
        out.append(_ge_item("scheduled", "cn_policy", p["title"], et, imp, _ge_heat_scheduled(ev),
                            country="中国", source="cn_policy"))
    return out


@app.route("/api/event_map/global_events")
def api_global_events():
    """全球重大事件: today = 结算窗口内排程 (imp>=2) ∪ 24h 突发 (衰减后 heat>=20) ∪ 巨头财报 ∪ 政策会议,
    按 heat 降序取 40; upcoming = 明天起 days 天内 (imp>=3) 按日期分组。只读库, 不请求外网。"""
    try:
        days = max(1, min(30, int(request.args.get("days", 14))))
    except (TypeError, ValueError):
        days = 14
    now = now_hkt()
    day = (now - timedelta(hours=6)).strftime("%Y-%m-%d")
    start, end = settlement_window(day)
    horizon = end + timedelta(days=days)

    sched = db.query(f"SELECT {_GE_COLS} FROM event_global WHERE kind='scheduled' "
                     f"AND event_time >= %s AND event_time < %s ORDER BY event_time", (start, horizon))
    brk = db.query(f"SELECT {_GE_COLS} FROM event_global WHERE kind='breaking' "
                   f"AND event_time >= %s AND event_time <= %s ORDER BY event_time DESC",
                   (now - timedelta(hours=24), now + timedelta(minutes=5)))
    try:
        extra_earnings = _ge_mega_earnings(start, horizon)
    except Exception as e:
        print(f"[WARN] global_events mega_earnings: {str(e).splitlines()[0]}")
        extra_earnings = []
    try:
        extra_policy = _ge_policy_events(start, horizon)
    except Exception as e:
        print(f"[WARN] global_events policy_events: {str(e).splitlines()[0]}")
        extra_policy = []
    extra = extra_earnings + extra_policy

    today, upcoming, breaking = [], {}, []
    for r in sched:
        try:
            it = _ge_row(r, now)
            et = _ge_dt(r["event_time"])
        except Exception as e:
            print(f"[WARN] global_events row skipped: {str(e).splitlines()[0]}")
            continue
        if et < end:
            if _ge_is_major(it):
                today.append(it)
        elif _ge_is_major(it):
            upcoming.setdefault(_ge_settle_key(et), []).append(it)
    for r in brk:
        try:
            it = _ge_row(r, now)
        except Exception as e:
            print(f"[WARN] global_events row skipped: {str(e).splitlines()[0]}")
            continue
        if it["heat"] >= _GE_BREAKING_MIN_HEAT and _ge_is_major(it):
            today.append(it)
        if _ge_is_breaking_news(it):
            breaking.append(it)
    for it in extra:
        et = _ge_dt(it["time"])
        if et < end:
            today.append(it)
        else:
            upcoming.setdefault(_ge_settle_key(et), []).append(it)
    today.sort(key=lambda x: -x["heat"])
    today = today[:_GE_TODAY_MAX]
    for k in upcoming:
        upcoming[k].sort(key=lambda x: x["time"])
    upcoming = dict(sorted(upcoming.items()))
    breaking.sort(key=lambda x: x["time"], reverse=True)
    breaking = _ge_dedupe_breaking(breaking)[:_GE_BREAKING_MAX]
    return jsonify({"time_hkt": _iso(now), "window_start": _iso(start), "window_end": _iso(end),
                    "days": days, "today": today, "upcoming": upcoming, "breaking": breaking,
                    "sources": _ge_load_health()})


# ═══ A-to-A: 放量事件接收 API (from 3404) ═══
import json as _json

VOLUME_EVENTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "volume_events")

@app.route("/api/events", methods=["POST"])
def api_receive_event():
    """接收外部项目推送的事件 (3404放量事件等)"""
    try:
        event = request.get_json(force=True)
        event_type = event.get("type", "unknown")
        date = event.get("date", datetime.now().strftime("%Y-%m-%d"))

        # 按类型+日期存储
        os.makedirs(VOLUME_EVENTS_DIR, exist_ok=True)
        fpath = os.path.join(VOLUME_EVENTS_DIR, f"{event_type}_{date}.json")

        # 读取已有事件
        events = []
        if os.path.exists(fpath):
            with open(fpath, encoding="utf-8") as f:
                events = _json.load(f)

        # 添加新事件 (去重: 同symbol+同status不重复)
        sig = f"{event.get('ticker')}_{event.get('status')}"
        existing_sigs = {f"{e.get('ticker')}_{e.get('status')}" for e in events}
        if sig not in existing_sigs:
            event["received_at"] = datetime.now().isoformat()
            events.append(event)

        with open(fpath, "w", encoding="utf-8") as f:
            _json.dump(events, f, ensure_ascii=False, indent=1)

        return jsonify({"ok": True, "count": len(events)})
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 400


@app.route("/api/events/<event_type>")
def api_list_events(event_type):
    """查询某类事件"""
    date = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))
    fpath = os.path.join(VOLUME_EVENTS_DIR, f"{event_type}_{date}.json")
    if os.path.exists(fpath):
        with open(fpath, encoding="utf-8") as f:
            return jsonify({"events": _json.load(f), "date": date})
    return jsonify({"events": [], "date": date})


@app.route("/")
@app.route("/event_map")
def event_map_page():
    """服务前端时间地图页面 (templates/event_map.html)"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "event_map.html")
    try:
        with open(path, "rb") as f:
            return Response(f.read(), mimetype="text/html")
    except FileNotFoundError:
        return "<html><body><h3>templates/event_map.html 不存在</h3></body></html>", 404


if __name__ == "__main__":
    print("Event Map API 启动于 0.0.0.0:3401")
    # 后台预热 (合并为一个线程, 避免两个线程并发打 4002 = 16 workers 全挂)
    # ⚠️ 4002 /history 并发>4 即大面积超时 (服务过载), 用并发3 + 每请求间隔, 极低速预热
    # ⚠️ 用 4002 同源 (tv4002_history_52w), 不用 yfinance — Yahoo 401 crumb 限流会卡死预热线程
    import threading

    # 启动即加载磁盘缓存 (上次预热结果), 页面秒开
    try:
        if os.path.exists(_WATCH_KW_FILE):
            with open(_WATCH_KW_FILE, "r", encoding="utf-8") as f:
                disk = json.load(f)
            import time as _t0
            _WATCH_KW_CACHE["data"] = disk.get("data", {})
            _WATCH_KW_CACHE["missing"] = disk.get("missing", [])
            # ts=当前时间: 视为新鲜, 请求不触发 4002; 预热线程稍后会慢慢刷新
            _WATCH_KW_CACHE["ts"] = _t0.time()
            _WATCH_KW_CACHE["retry_ts"] = _t0.time()
            print(f"加载磁盘 52周缓存: {len(disk.get('data', {}))} symbols")
    except Exception as e:
        print(f"磁盘缓存加载失败: {str(e).splitlines()[0]}")

    def _warmup():
        global _WATCH_PREPARING
        try:
            import time as _t
            from concurrent.futures import ThreadPoolExecutor, as_completed
            _t.sleep(2)
            _WATCH_PREPARING = True
            # 1) 指数关键价位 (assets API 用)
            idx_syms = [s for s, _, _ in [
                ("SPTRD",0,0),("NASDAQ",0,0),("DOW",0,0),("RUSSELL",0,0),("NIKKEI",0,0),("TOPIX",0,0),
                ("HSI",0,0),("HHI",0,0),("HTI",0,0),("TAIWAN",0,0),("KOSPI",0,0),("ASX",0,0),
                ("SENSEX",0,0),("FTSE",0,0),("DAX",0,0),("CAC",0,0),("SMI",0,0),("AEX",0,0),
                ("CN50",0,0),("STXE",0,0),("IBEX",0,0),("IT40",0,0)]]
            cache = {"ts": _t.time(), "data": {}}
            with ThreadPoolExecutor(max_workers=3) as ex:
                futs = {ex.submit(tv4002_history_52w, s, 25): s for s in idx_syms}
                for f in as_completed(futs):
                    try: cache["data"][futs[f]] = f.result() or {}
                    except Exception: cache["data"][futs[f]] = {}
            setattr(api_assets, "_kw_cache", cache)
            ok = sum(1 for v in cache["data"].values() if v)
            print(f"预热完成: {ok}/{len(idx_syms)} 指数关键价位缓存 (4002同源)")
            # 2) watch 52周缓存
            watch_syms = [
                ("SPTRD","标普500"),("NASDAQ","纳斯达克"),("DOW","道琼斯"),("RUSSELL","罗素2000"),
                ("NIKKEI","日经225"),("HSI","恒生"),("HHI","恒生国企"),("HTI","恒生科技"),
                ("TAIWAN","台湾加权"),("KOSPI","韩国综合"),("ASX","澳洲200"),("SENSEX","印度SENSEX"),
                ("FTSE","英国100"),("DAX","德国40"),("CAC","法国40"),("CN50","中国A50"),("HK50","恒生HS50"),
                ("CFDGOLD","黄金"),("CFDSILVER","白银"),("COPPER","铜"),("CL","WTI原油"),
                ("LCO","布伦特原油"),("NG","天然气"),("RB","汽油"),("WHEAT","小麦"),
                ("C","玉米"),("S","大豆"),("COTTON","棉花"),
                ("JPY_FX","USDJPY"),("CNH_FX","USDCNH"),("EUR_FX","EURUSD"),
                ("GBP_FX","GBPUSD"),("USDX_FX","美元指数"),
                ("US_BOND","美债"),("EURO_BOND","欧债"),("CN_BOND","中债"),("JP_BOND","日债"),
                ("BTC_SPOT","BTC"),("ETH_SPOT","ETH"),("SOL_SPOT","SOL"),
                ("BNB_SPOT","BNB"),("XRP_SPOT","XRP"),("DOGE_SPOT","DOGE"),
            ]
            def _load(sym):
                try:
                    v = tv4002_history_52w(sym, 25) or {}
                    # 4002 无数据: 尝试 yfinance 兜底
                    if not v:
                        yf_v = _yf_history_52w(sym)
                        if yf_v:
                            v = yf_v
                    return v
                except Exception:
                    return {}
            with ThreadPoolExecutor(max_workers=3) as ex:
                futs = {ex.submit(_load, s): s for s, _ in watch_syms}
                data = {}
                missing = set()
                for f in as_completed(futs):
                    try:
                        v = f.result()
                        if v:
                            data[futs[f]] = v
                        else:
                            missing.add(futs[f])
                    except Exception:
                        missing.add(futs[f])
            _WATCH_KW_CACHE["data"] = data
            _WATCH_KW_CACHE["missing"] = sorted(missing)
            _WATCH_KW_CACHE["ts"] = _t.time()
            _WATCH_KW_CACHE["retry_ts"] = _t.time()
            # 落盘 (下次重启秒载)
            try:
                with open(_WATCH_KW_FILE, "w", encoding="utf-8") as f:
                    json.dump({"data": data, "missing": sorted(missing)}, f, ensure_ascii=False)
            except Exception as e:
                print(f"52周缓存落盘失败: {str(e).splitlines()[0]}")
            print(f"watch 52周预热: {len(data)}/{len(watch_syms)} ok, missing {len(missing)}")
        except Exception as e:
            print(f"预热失败: {str(e).splitlines()[0]}")
        finally:
            _WATCH_PREPARING = False
    threading.Thread(target=_warmup, daemon=True).start()
    app.run(host="0.0.0.0", port=3401, threaded=True)
