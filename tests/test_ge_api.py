# -*- coding: utf-8 -*-
import json
import os
import sys
import unittest
from datetime import datetime
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import server  # noqa: E402  (导入会起后台预热线程, daemon, 不影响测试)


NOW = datetime(2026, 9, 17, 15, 20, 0)


def _row(**kw):
    base = {"kind": "scheduled", "category": "central_bank", "title": "t", "summary": None, "country": None,
            "event_time": "2026-09-17 20:30:00", "importance": 2, "heat_base": 30, "heat_score": 30,
            "reading_num": None, "expected": None, "previous": None, "actual": None, "tickers": None,
            "source": "wscn_calendar", "url": None}
    base.update(kw)
    return base


class GlobalEventsApiTest(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()

    def _get(self, rows_by_kind, earnings_rows=(), policy=(), health=None, qs=""):
        def fake_query(sql, params=None):
            if "FROM event_global" in sql:
                if "kind='breaking'" in sql:
                    rows = rows_by_kind.get("breaking", [])
                    if params and len(params) >= 2:
                        cutoff = params[1].strftime("%Y-%m-%d %H:%M:%S")
                        rows = [r for r in rows if r["event_time"] <= cutoff]
                    return rows
                return rows_by_kind.get("scheduled", [])
            if "FROM event_earnings" in sql:
                return list(earnings_rows)
            return []

        with mock.patch.object(server, "now_hkt", return_value=NOW), \
             mock.patch.object(server.db, "query", side_effect=fake_query), \
             mock.patch.object(server, "_ge_load_static", return_value=list(policy)), \
             mock.patch.object(server, "_ge_load_health", return_value=health or {}):
            r = self.client.get("/api/event_map/global_events" + qs)
        self.assertEqual(r.status_code, 200)
        return json.loads(r.data.decode("utf-8"))

    def test_window_and_split(self):
        rows = {"scheduled": [
            _row(title="今日 imp4", event_time="2026-09-17 20:30:00", importance=4, heat_score=95),
            _row(title="今日 imp3 过滤", event_time="2026-09-17 21:00:00", importance=3, heat_score=60),
            _row(title="明日 imp4", event_time="2026-09-18 07:30:00", importance=4, heat_score=95),
            _row(title="明日 imp3 过滤", event_time="2026-09-18 08:00:00", importance=3, heat_score=60),
        ]}
        j = self._get(rows)
        self.assertEqual(j["window_start"], "2026-09-17 06:00:00")
        self.assertEqual(j["window_end"], "2026-09-18 06:00:00")
        self.assertEqual([x["title"] for x in j["today"]], ["今日 imp4"])
        self.assertEqual(list(j["upcoming"].keys()), ["2026-09-18"])
        self.assertEqual([x["title"] for x in j["upcoming"]["2026-09-18"]], ["明日 imp4"])
        self.assertEqual(j["days"], 14)

    def test_breaking_decay_and_threshold_and_sort(self):
        rows = {
            "scheduled": [_row(title="排程", event_time="2026-09-17 20:30:00", importance=4, heat_score=95)],
            "breaking": [
                _row(kind="breaking", importance=4, title="新鲜", event_time="2026-09-17 15:20:00", heat_base=80, heat_score=80, source="wscn_live"),
                _row(kind="breaking", importance=4, title="三小时前", event_time="2026-09-17 12:20:00", heat_base=80, heat_score=80, source="wscn_live"),
                _row(kind="breaking", importance=4, title="太旧", event_time="2026-09-17 03:20:00", heat_base=100, heat_score=100, source="wscn_live"),
                _row(kind="breaking", importance=4, title="未来", event_time="2026-09-18 03:00:00", heat_base=100, heat_score=100, source="wscn_live"),
            ],
        }
        j = self._get(rows)
        titles = [x["title"] for x in j["today"]]
        self.assertEqual(titles, ["排程", "新鲜", "三小时前"])   # 100*0.5^4 = 6.25 < 20 被过滤; 未来行超出上限被 SQL 过滤
        heats = {x["title"]: x["heat"] for x in j["today"]}
        self.assertEqual(heats["新鲜"], 80.0)
        self.assertEqual(heats["三小时前"], 40.0)
        self.assertNotIn("未来", titles)

    def test_mega_earnings_and_policy_merged(self):
        earnings = [{"ticker": "NVDA", "event_time": "2026-09-18 04:00:00", "title": "NVDA 财报"}]
        policy = [{"date": "2026-09-25", "time": "16:00", "title": "中央政治局会议（预计）", "importance": 4, "country": "中国"},
                  {"date": "2026-12-01", "time": "09:00", "title": "超出范围", "importance": 4, "country": "中国"}]
        j = self._get({"scheduled": []}, earnings_rows=earnings, policy=policy)
        self.assertEqual(j["today"][0]["title"], "英伟达 (NVDA) 财报")
        self.assertEqual(j["today"][0]["category"], "earnings")
        self.assertEqual(j["today"][0]["heat"], 100.0)
        self.assertEqual(j["today"][0]["tickers"], "NVDA")
        self.assertIn("2026-09-25", j["upcoming"])
        self.assertNotIn("2026-12-01", j["upcoming"])
        self.assertEqual(j["upcoming"]["2026-09-25"][0]["source"], "cn_policy")

    def test_days_clamped_and_sources_passthrough(self):
        h = {"cls_roll": {"ok": False, "last_error": "404"}}
        j = self._get({}, health=h, qs="?days=99")
        self.assertEqual(j["days"], 30)
        self.assertEqual(j["sources"], h)
        j = self._get({}, qs="?days=abc")
        self.assertEqual(j["days"], 14)

    def test_today_capped_at_40(self):
        rows = {"scheduled": [_row(title="e%d" % i, event_time="2026-09-17 20:30:00", importance=4, heat_score=i)
                              for i in range(60)]}
        j = self._get(rows)
        self.assertEqual(len(j["today"]), 40)
        self.assertEqual(j["today"][0]["title"], "e59")

    def test_only_four_star_major_categories_shown(self):
        rows = {
            "scheduled": [
                _row(title="华为全联接大会", category="tech_event", importance=4, event_time="2026-09-17 12:02:00", heat_score=90),
                _row(title="东博会", category="other", importance=3, event_time="2026-09-17 12:02:00", heat_score=65),
                _row(title="恒生科技指数调整", category="other", importance=4, event_time="2026-09-18 00:00:00", heat_score=90),
                _row(title="云栖大会", category="tech_event", importance=4, event_time="2026-09-22 12:02:00", heat_score=90),
                _row(title="非农", category="jobs", importance=4, event_time="2026-10-02 20:30:00", heat_score=100),
            ],
            "breaking": [
                _row(kind="breaking", title="午间涨停分析", category="other", importance=4, event_time="2026-09-17 15:00:00", heat_base=80, heat_score=80),
                _row(kind="breaking", title="美联储加息", category="central_bank", importance=3, event_time="2026-09-17 15:00:00", heat_base=80, heat_score=80),
                _row(kind="breaking", title="沙特管道遇袭", category="energy_supply", importance=4, event_time="2026-09-17 15:00:00", heat_base=80, heat_score=80),
            ],
        }
        j = self._get(rows)
        self.assertEqual([x["title"] for x in j["today"]], ["沙特管道遇袭"])
        self.assertEqual(list(j["upcoming"].keys()), ["2026-10-02"])

    def test_db_failure_returns_empty_not_500(self):
        with mock.patch.object(server, "now_hkt", return_value=NOW), \
             mock.patch.object(server.db, "query", return_value=[]), \
             mock.patch.object(server, "_ge_load_static", return_value=[]), \
             mock.patch.object(server, "_ge_load_health", return_value={}):
            r = self.client.get("/api/event_map/global_events")
        self.assertEqual(r.status_code, 200)
        j = json.loads(r.data.decode("utf-8"))
        self.assertEqual(j["today"], [])
        self.assertEqual(j["upcoming"], {})

    def test_window_before_0600_uses_previous_settlement_day(self):
        early = datetime(2026, 9, 17, 3, 0, 0)
        rows = {"scheduled": [
            _row(title="凌晨事件", event_time="2026-09-17 02:00:00", importance=4, heat_score=50),
        ]}

        def fake_query(sql, params=None):
            if "FROM event_global" in sql:
                return rows.get("breaking" if "kind='breaking'" in sql else "scheduled", [])
            return []

        with mock.patch.object(server, "now_hkt", return_value=early), \
             mock.patch.object(server.db, "query", side_effect=fake_query), \
             mock.patch.object(server, "_ge_load_static", return_value=[]), \
             mock.patch.object(server, "_ge_load_health", return_value={}):
            r = self.client.get("/api/event_map/global_events")
        self.assertEqual(r.status_code, 200)
        j = json.loads(r.data.decode("utf-8"))
        self.assertEqual(j["window_start"], "2026-09-16 06:00:00")
        self.assertEqual(j["window_end"], "2026-09-17 06:00:00")
        self.assertEqual([x["title"] for x in j["today"]], ["凌晨事件"])

    def test_upcoming_grouped_by_settlement_day(self):
        rows = {"scheduled": [
            _row(title="09-19凌晨 属09-18交割日", event_time="2026-09-19 02:00:00", importance=4, heat_score=50),
            _row(title="09-19上午 属09-19交割日", event_time="2026-09-19 07:00:00", importance=4, heat_score=50),
        ]}
        j = self._get(rows)
        self.assertEqual([x["title"] for x in j["upcoming"]["2026-09-18"]], ["09-19凌晨 属09-18交割日"])
        self.assertEqual([x["title"] for x in j["upcoming"]["2026-09-19"]], ["09-19上午 属09-19交割日"])

    def test_malformed_row_skipped_not_500(self):
        rows = {"scheduled": [
            _row(title="好行", event_time="2026-09-17 20:30:00", importance=4, heat_score=30),
            _row(title="坏行", event_time="garbage", importance=4, heat_score=30),
        ]}
        j = self._get(rows)
        self.assertEqual([x["title"] for x in j["today"]], ["好行"])

    def test_policy_source_failure_isolated_from_earnings(self):
        earnings = [{"ticker": "NVDA", "event_time": "2026-09-18 04:00:00", "title": "NVDA 财报"}]

        def fake_query(sql, params=None):
            if "FROM event_global" in sql:
                return []
            if "FROM event_earnings" in sql:
                return list(earnings)
            return []

        with mock.patch.object(server, "now_hkt", return_value=NOW), \
             mock.patch.object(server.db, "query", side_effect=fake_query), \
             mock.patch.object(server, "_ge_load_static", side_effect=ValueError("bad json")), \
             mock.patch.object(server, "_ge_load_health", return_value={}):
            r = self.client.get("/api/event_map/global_events")
        self.assertEqual(r.status_code, 200)
        j = json.loads(r.data.decode("utf-8"))
        self.assertEqual(j["today"][0]["title"], "英伟达 (NVDA) 财报")


if __name__ == "__main__":
    unittest.main()
