# -*- coding: utf-8 -*-
"""全球重大事件 — 数据源解析与抓取。
parse_* 只做纯解析 (可离线测试); fetch_* 走网络, 失败抛异常由调用方记录 health。
所有时间转成 naive HKT datetime。
"""
import email.utils
import hashlib
import html
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .classify import categorize, normalize_importance, load_static

NET_TIMEOUT = 10
UA_HEADERS = {"User-Agent": "Mozilla/5.0"}
HKT_OFFSET = timedelta(hours=8)

WSCN_CALENDAR_URL = "https://api-one-wscn.awtmt.com/apiv1/finance/macrodatas"
WSCN_LIVE_URL = "https://api-one.wallstcn.com/apiv1/content/lives"
FF_THISWEEK_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FF_NEXTWEEK_URL = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"
CLS_ROLL_URL = "https://www.cls.cn/v1/roll/get_roll_list"
CLS_HOT_URL = "https://www.cls.cn/v2/article/hot/list"
CLS_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.cls.cn/telegraph"}
GNEWS_URL = "https://news.google.com/rss/search"

FF_COUNTRY = {"USD": "美国", "EUR": "欧元区", "GBP": "英国", "JPY": "日本", "CNY": "中国",
              "CAD": "加拿大", "AUD": "澳大利亚", "NZD": "新西兰", "CHF": "瑞士", "All": "全球"}
_FF_TITLE_MAP = load_static("ff_title_map.json")


# ── 时间 / 工具 ───────────────────────────────────────────────────────────────
def ts_to_hkt(ts):
    """unix 秒 → naive HKT。"""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(tzinfo=None) + HKT_OFFSET


def iso_to_hkt(s):
    """ISO8601 (带或不带偏移) → naive HKT。不带偏移视为 UTC。"""
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(tzinfo=None) + HKT_OFFSET


def rfc822_to_hkt(s):
    """RFC 822 (RSS pubDate) → naive HKT。"""
    dt = email.utils.parsedate_to_datetime(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(tzinfo=None) + HKT_OFFSET


def make_dedup_key(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def hkt_to_ts(d):
    """naive HKT datetime → unix 秒。"""
    return int((d - HKT_OFFSET).replace(tzinfo=timezone.utc).timestamp())


def _s(v, limit=64):
    """字符串清洗: None/空/nan → None, 其余 strip + 截断。"""
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "nan", "None", "NaN", "null"):
        return None
    return s[:limit]


def _event(**kw):
    base = {"kind": "breaking", "category": "other", "title": "", "summary": None, "country": None,
            "event_time": None, "importance": 1, "heat_base": 0.0, "heat_score": 0.0,
            "reading_num": None, "expected": None, "previous": None, "actual": None,
            "tickers": None, "source": "", "source_id": None, "url": None, "dedup_key": ""}
    base.update(kw)
    base["title"] = (base["title"] or "")[:255]
    if base["summary"]:
        base["summary"] = base["summary"][:500]
    return base


# ── HTTP ─────────────────────────────────────────────────────────────────────
def http_get(url, headers=None, timeout=NET_TIMEOUT):
    h = dict(UA_HEADERS)
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def http_json(url, headers=None):
    return json.loads(http_get(url, headers=headers).decode("utf-8"))


def cls_signed_query(extra):
    """财联社签名: 固定参数 + 业务参数按 key 排序, sign = md5(sha1(query))。"""
    params = {"app": "CailianpressWeb", "os": "web", "sv": "7.7.5"}
    params.update(extra or {})
    qs = urllib.parse.urlencode(sorted(params.items()))
    sign = hashlib.md5(hashlib.sha1(qs.encode("utf-8")).hexdigest().encode("utf-8")).hexdigest()
    return qs + "&sign=" + sign


# ── S1 华尔街见闻 宏观日历 ───────────────────────────────────────────────────
def parse_wscn_calendar(payload):
    out = []
    items = ((payload or {}).get("data") or {}).get("items") or []
    for it in items:
        title = _s(it.get("title"), 255)
        if not title or it.get("public_date") is None:
            continue
        out.append(_event(
            kind="scheduled", category=categorize(title), title=title,
            country=_s(it.get("country"), 32), event_time=ts_to_hkt(it["public_date"]),
            importance=normalize_importance("wscn_calendar", it.get("importance")),
            expected=_s(it.get("forecast")), previous=_s(it.get("previous")), actual=_s(it.get("actual")),
            source="wscn_calendar", source_id=str(it.get("id")), url=_s(it.get("uri"), 512),
            dedup_key=make_dedup_key("wscn_calendar", it.get("id")),
        ))
    return out


def calendar_chunks(start, days=14, chunk_days=7):
    """[start, start+days) 按 chunk_days 切成 (unix_start, unix_end) 列表, 每段 ≤ chunk_days。"""
    out = []
    cur = start
    end = start + timedelta(days=days)
    while cur < end:
        nxt = min(cur + timedelta(days=chunk_days), end)
        out.append((hkt_to_ts(cur), hkt_to_ts(nxt)))
        cur = nxt
    return out


def fetch_wscn_calendar(start, days=14):
    """分段拉取; 部分段失败返回已成功部分并打印首行; 全部失败抛最后一个异常。"""
    evs, ok, last_err = [], 0, None
    for a, b in calendar_chunks(start, days=days):
        url = "%s?start=%d&end=%d" % (WSCN_CALENDAR_URL, a, b)
        try:
            evs.extend(parse_wscn_calendar(http_json(url)))
            ok += 1
        except Exception as e:
            last_err = e
            print("WARN wscn_calendar chunk %d-%d: %s" % (a, b, str(e).splitlines()[0]))
    if ok == 0 and last_err is not None:
        raise last_err
    return evs


# ── S1b ForexFactory 官方 JSON ───────────────────────────────────────────────
def parse_ff_calendar(items):
    out = []
    for it in items or []:
        impact = it.get("impact")
        imp = normalize_importance("ff_calendar", impact)
        if imp < 2:
            continue
        raw_title = _s(it.get("title"), 255)
        if not raw_title or not it.get("date"):
            continue
        title = _FF_TITLE_MAP.get(raw_title, raw_title)
        code = it.get("country") or ""
        out.append(_event(
            kind="scheduled", category=categorize(raw_title + " " + title), title=title,
            country=FF_COUNTRY.get(code, code or None), event_time=iso_to_hkt(it["date"]),
            importance=imp, expected=_s(it.get("forecast")), previous=_s(it.get("previous")),
            source="ff_calendar", source_id=None, url=None,
            dedup_key=make_dedup_key("ff_calendar", code, raw_title, it["date"]),
        ))
    return out


def fetch_ff_calendar():
    """thisweek 必须成功; nextweek 失败仅打印。"""
    evs = parse_ff_calendar(http_json(FF_THISWEEK_URL))
    try:
        evs.extend(parse_ff_calendar(http_json(FF_NEXTWEEK_URL)))
    except Exception as e:
        print("WARN ff_calendar nextweek: %s" % str(e).splitlines()[0])
    return evs
