#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
事件管理系统 (时间地图 :3401) — 全球重大事件采集脚本
====================================================
来源: 华尔街见闻日历/快讯, ForexFactory 日历, 财联社电报/热门, Google News RSS, (可选) DailyHotApi。
写入 event_global (dedup_key 幂等)。各来源独立容错, 状态写 data/global_events_health.json。
所有网络请求 timeout<=10s, 异常只 print 首行, 进程永远退出码 0 (与其它采集脚本一致)。

用法:
  python3 collect_global_events.py --calendar   # 未来 14 天已排程事件 (cron */30)
  python3 collect_global_events.py --breaking   # 突发快讯 (cron */3)
  python3 collect_global_events.py --all        # 两者都跑 (首次初始化)
  python3 collect_global_events.py --check      # 只测各来源连通性, 不写库
"""
import argparse
import os
import sys
import warnings
from datetime import datetime, timedelta, timezone

warnings.filterwarnings("ignore")  # 与其它采集脚本一致

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                    # noqa: E402
import db_writer                                 # noqa: E402
from global_events import sources as S           # noqa: E402
from global_events import merge as M             # noqa: E402
from global_events import scoring as SC          # noqa: E402
from global_events.health import update_health, HEALTH_PATH as _HP  # noqa: E402

HEALTH_PATH = _HP
CALENDAR_DAYS = 16   # API 的 upcoming 上界 = 明日 06:00 + 14 天, 比今天 00:00 起算多 ~1.3 天, 多拉 2 天防止末尾漏事件
BREAKING_LOOKBACK_H = 24


def _err(e):
    return str(e).splitlines()[0] if str(e) else e.__class__.__name__


def now_hkt():
    # datetime.utcnow() 已弃用 (Python 3.12); 用 now(timezone.utc) 去掉 tzinfo 等价替代
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=8)


def _health(source, ok, count=0, error=None):
    update_health(source, ok, count=count, error=error, path=HEALTH_PATH)


def _pull(source, fn):
    """调用 fetch, 记录 health, 失败返回 []。"""
    try:
        evs = fn()
        _health(source, True, count=len(evs))
        print("OK %s: %d" % (source, len(evs)))
        return evs
    except Exception as e:
        _health(source, False, error=_err(e))
        print("ERR %s: %s" % (source, _err(e)))
        return []


# ── --calendar ───────────────────────────────────────────────────────────────
def run_calendar(now):
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    wscn = _pull("wscn_calendar", lambda: S.fetch_wscn_calendar(start, days=CALENDAR_DAYS))
    ff = _pull("ff_calendar", S.fetch_ff_calendar)
    merged = M.merge_ff_into_wscn(wscn, ff)
    for ev in merged:
        ev["heat_base"] = ev["heat_score"] = SC.heat_scheduled(ev)
    if not merged:
        return 0
    n = db_writer.save_global_events(merged)
    print("calendar saved %d/%d" % (n, len(merged)))
    return n


# ── --breaking ───────────────────────────────────────────────────────────────
def _recent_titles(now):
    """DB 里最近 24h 的 breaking (title, source), 用于多源同现。DB 失败返回 []。"""
    try:
        rows = db_writer.DB.query(
            "SELECT title, source FROM event_global WHERE kind='breaking' AND event_time >= %s",
            [(now - timedelta(hours=BREAKING_LOOKBACK_H)).isoformat()])
        return [(r.get("title") or "", r.get("source") or "") for r in rows]
    except Exception as e:
        print("WARN recent titles: %s" % _err(e))
        return []


def run_breaking(now):
    batches = {}
    batches["wscn_live"] = _pull("wscn_live", S.fetch_wscn_live)
    if config.GLOBAL_EVENTS_CLS_ENABLED:
        batches["cls_roll"] = _pull("cls_roll", S.fetch_cls_roll)
        batches["cls_hot"] = _pull("cls_hot", S.fetch_cls_hot)
    batches["gnews"] = _pull("gnews", lambda: M.cluster_gnews(S.fetch_gnews()))
    hot_titles = []
    if config.GLOBAL_EVENTS_HOT_URL:
        hot_titles = _pull("dailyhot", lambda: S.fetch_dailyhot(config.GLOBAL_EVENTS_HOT_URL))

    all_new = [e for evs in batches.values() for e in evs]
    if not all_new:
        return 0
    others = _recent_titles(now) + [(e["title"], e["source"]) for e in all_new]
    for source, evs in batches.items():
        readings = [e["reading_num"] for e in evs if e.get("reading_num") is not None]
        for ev in evs:
            my_others = [(t, s) for t, s in others if s != source]
            ev["heat_base"] = SC.heat_breaking_base(ev, readings, my_others, hot_titles)
            ev["heat_score"] = SC.apply_decay(ev["heat_base"], ev["event_time"], now)
    n = db_writer.save_global_events(all_new)
    print("breaking saved %d/%d" % (n, len(all_new)))
    return n


# ── --check ──────────────────────────────────────────────────────────────────
def run_check():
    """返回 {source: (ok|None, count, error)}; None = 未启用。"""
    now = now_hkt()
    probes = [
        ("wscn_calendar", lambda: S.fetch_wscn_calendar(now, days=7)),
        ("ff_calendar", S.fetch_ff_calendar),
        ("wscn_live", lambda: S.fetch_wscn_live(limit=5)),
    ]
    out = {}
    for name, fn in probes:
        try:
            out[name] = (True, len(fn()), None)
        except Exception as e:
            out[name] = (False, 0, _err(e))
    if config.GLOBAL_EVENTS_CLS_ENABLED:
        for name, fn in (("cls_roll", lambda: S.fetch_cls_roll(rn=5)), ("cls_hot", S.fetch_cls_hot)):
            try:
                out[name] = (True, len(fn()), None)
            except Exception as e:
                out[name] = (False, 0, _err(e))
    else:
        out["cls_roll"] = (None, 0, "disabled")
        out["cls_hot"] = (None, 0, "disabled")
    try:
        out["gnews"] = (True, len(S.fetch_gnews()), None)
    except Exception as e:
        out["gnews"] = (False, 0, _err(e))
    if config.GLOBAL_EVENTS_HOT_URL:
        try:
            out["dailyhot"] = (True, len(S.fetch_dailyhot(config.GLOBAL_EVENTS_HOT_URL)), None)
        except Exception as e:
            out["dailyhot"] = (False, 0, _err(e))
    else:
        out["dailyhot"] = (None, 0, "disabled")
    print("flags: GLOBAL_EVENTS_CLS_ENABLED=%s GLOBAL_EVENTS_HOT_URL=%s" %
          (bool(config.GLOBAL_EVENTS_CLS_ENABLED), config.GLOBAL_EVENTS_HOT_URL or "-"))
    for k, (ok, cnt, err) in out.items():
        print("%-14s %s %s" % (k, "OK  " if ok else ("--  " if ok is None else "FAIL"), cnt if ok else (err or "")))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--calendar", action="store_true")
    ap.add_argument("--breaking", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.check:
            run_check()
            return 0
        now = now_hkt()
        if a.all or a.calendar:
            run_calendar(now)
        if a.all or a.breaking:
            run_breaking(now)
        if not (a.all or a.calendar or a.breaking):
            ap.print_help()
    except Exception as e:
        print("FATAL: %s" % _err(e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
