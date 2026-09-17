# -*- coding: utf-8 -*-
"""全球重大事件 — 热度打分 (v1 纯规则, 0-100)。见 spec §5。"""
import re

from .classify import mega_tickers_in
from .merge import similarity

KEY_SCHEDULED = {"central_bank", "inflation", "jobs", "cn_policy", "earnings"}
KEY_BREAKING = {"geopolitics", "fin_risk", "central_bank", "cn_policy", "energy_supply"}
KEY_COUNTRIES = {"美国", "中国", "USD", "CNY"}
SCHED_BASE = {4: 85, 3: 60, 2: 30, 1: 10}
BREAK_BASE = {4: 70, 3: 50, 2: 25, 1: 8}
HALF_LIFE_H = 3.0
COOCCUR_THRESHOLD = 0.6
SOCIAL_MIN_SHARED_BIGRAMS = 3

_NUM = re.compile(r"-?\d+(?:\.\d+)?")
_CJK = re.compile(r"[一-鿿]")


def _num(v):
    if v is None:
        return None
    s = re.sub(r"(?<=\d),(?=\d)", "", str(v))
    m = _NUM.search(s)
    return float(m.group()) if m else None


def surprise_pct(expected, actual):
    e, a = _num(expected), _num(actual)
    if e is None or a is None or e == 0:
        return None
    return abs(a - e) / abs(e) * 100.0


def heat_scheduled(ev):
    imp = int(ev.get("importance") or 1)
    h = SCHED_BASE.get(imp, 10)
    if ev.get("category") in KEY_SCHEDULED:
        h += 10
    if (ev.get("country") or "") in KEY_COUNTRIES:
        h += 5
    sp = surprise_pct(ev.get("expected"), ev.get("actual"))
    if sp is not None and sp >= 5.0:
        h += 15
    return float(min(100, h))


def percentile_rank(values, v):
    n = len(values)
    if n <= 1:
        return 0.5
    return sum(1 for x in values if x < v) / float(n - 1)


def _is_cjk(s):
    return _CJK.search(s or "") is not None


def has_cooccurrence(title, source, others):
    lang = _is_cjk(title)
    for o_title, o_source in others:
        if o_source == source or _is_cjk(o_title) != lang:
            continue
        if similarity(title, o_title) >= COOCCUR_THRESHOLD:
            return True
    return False


def cjk_bigrams(s):
    chars = [c for c in (s or "") if _CJK.match(c)]
    return {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}


def social_hit(title, hot_titles):
    b = cjk_bigrams(title)
    if not b:
        return False
    return any(len(b & cjk_bigrams(h)) >= SOCIAL_MIN_SHARED_BIGRAMS for h in hot_titles or [])


def heat_breaking_base(ev, batch_reading, others, hot_titles):
    imp = int(ev.get("importance") or 1)
    h = float(BREAK_BASE.get(imp, 8))
    if ev.get("reading_num") is not None and batch_reading:
        h += 20.0 * percentile_rank(batch_reading, ev["reading_num"])
    if has_cooccurrence(ev.get("title", ""), ev.get("source", ""), others or []):
        h += 15
    if ev.get("category") in KEY_BREAKING:
        h += 10
    if mega_tickers_in(ev.get("title", ""), ev.get("tickers")):
        h += 10
    if hot_titles and social_hit(ev.get("title", ""), hot_titles):
        h += 15
    return float(min(100.0, h))


def apply_decay(base, event_time, now):
    age_h = max(0.0, (now - event_time).total_seconds() / 3600.0)
    return round(max(0.0, min(100.0, float(base) * 0.5 ** (age_h / HALF_LIFE_H))), 2)
