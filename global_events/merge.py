# -*- coding: utf-8 -*-
"""全球重大事件 — 多源合并 (纯函数)。"""
import difflib
from datetime import timedelta

_SETTLE = timedelta(hours=6)


def _settle_day(dt):
    """HKT 06:00 交割日: 06:00 前算前一天。"""
    return (dt - _SETTLE).date()

from .classify import normalize_importance


def similarity(a, b):
    return difflib.SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def merge_ff_into_wscn(wscn, ff, window_min=30):
    """ForexFactory 只补漏: 同国家 + 同分类 + 时间差 ≤ window_min 分钟 已有 wscn 条目 → 丢弃。
    央行类 (central_bank) 一天一次, 见闻常用占位时间 (12:02), 改为同国家 + 同交割日即视为重复。"""
    out = list(wscn)
    win = timedelta(minutes=window_min)
    for f in ff:
        dup = False
        for w in wscn:
            if w.get("country") != f.get("country") or w.get("category") != f.get("category"):
                continue
            if f.get("category") == "central_bank":
                if _settle_day(w["event_time"]) == _settle_day(f["event_time"]):
                    dup = True
                    break
            elif abs(w["event_time"] - f["event_time"]) <= win:
                dup = True
                break
        if not dup:
            out.append(f)
    return out


def cluster_gnews(events, threshold=0.55):
    """按标题相似度贪心聚簇; 簇代表取最早一条, reading_num = 簇大小, importance 随簇大小重算。"""
    reps = []
    for e in sorted(events, key=lambda x: x["event_time"]):
        for r in reps:
            if similarity(r["title"], e["title"]) >= threshold:
                r["reading_num"] = (r.get("reading_num") or 0) + (e.get("reading_num") or 1)
                break
        else:
            c = dict(e)
            c["reading_num"] = e.get("reading_num") or 1
            reps.append(c)
    for r in reps:
        r["importance"] = normalize_importance("gnews", r["reading_num"])
    return reps
