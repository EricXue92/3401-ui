# -*- coding: utf-8 -*-
"""全球重大事件 — 各来源健康状态 (data/global_events_health.json)。"""
import json
import os
from datetime import datetime, timedelta, timezone

from . import PROJECT_ROOT

HEALTH_PATH = os.path.join(PROJECT_ROOT, "data", "global_events_health.json")


def _now_hkt_str():
    # datetime.utcnow() 已弃用 (Python 3.12); 用 now(timezone.utc) 去掉 tzinfo 等价替代
    utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    return (utc_naive + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


def load_health(path=None):
    p = path or HEALTH_PATH
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def update_health(source, ok, count=0, error=None, path=None):
    """合并写入一个来源的状态, 返回整份 health dict。"""
    p = path or HEALTH_PATH
    h = load_health(p)
    cur = h.get(source) or {"ok": False, "last_success": None, "last_error": None, "count": 0}
    cur["ok"] = bool(ok)
    cur["checked_at"] = _now_hkt_str()
    if ok:
        cur["last_success"] = cur["checked_at"]
        cur["count"] = int(count or 0)
        cur["last_error"] = None
    else:
        cur["last_error"] = (str(error).splitlines()[0] if error else "unknown")[:200]
    h[source] = cur
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(h, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
    except Exception as e:
        print("WARN health write: %s" % str(e).splitlines()[0])
    return h
