# -*- coding: utf-8 -*-
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from global_events import health as H   # noqa: E402
import db_writer                        # noqa: E402
import collect_global_events as C       # noqa: E402


def _ev(**kw):
    base = {"kind": "breaking", "category": "other", "title": "t", "summary": None, "country": None,
            "event_time": datetime(2026, 9, 17, 12, 0), "importance": 2, "heat_base": 0.0, "heat_score": 0.0,
            "reading_num": None, "expected": None, "previous": None, "actual": None, "tickers": None,
            "source": "wscn_live", "source_id": "1", "url": None, "dedup_key": "k1"}
    base.update(kw)
    return base


class HealthTest(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "h.json")
            H.update_health("wscn_live", True, count=5, path=p)
            H.update_health("cls_roll", False, error="HTTP 404", path=p)
            h = H.load_health(path=p)
        self.assertTrue(h["wscn_live"]["ok"])
        self.assertEqual(h["wscn_live"]["count"], 5)
        self.assertIsNotNone(h["wscn_live"]["last_success"])
        self.assertFalse(h["cls_roll"]["ok"])
        self.assertEqual(h["cls_roll"]["last_error"], "HTTP 404")
        self.assertIsNone(h["cls_roll"]["last_success"])

    def test_missing_file(self):
        self.assertEqual(H.load_health(path="/nonexistent/x.json"), {})


class SaveTest(unittest.TestCase):
    def test_upsert_sql_and_params(self):
        with mock.patch.object(db_writer.DB, "execute", return_value=1) as m:
            n = db_writer.save_global_events([_ev(heat_base=42.5, heat_score=40.0)])
        self.assertEqual(n, 1)
        sql, params = m.call_args[0]
        self.assertIn("INSERT INTO event_global", sql)
        self.assertIn("ON DUPLICATE KEY UPDATE", sql)
        self.assertIn("heat_base=VALUES(heat_base)", sql)
        self.assertEqual(params[0], "breaking")
        self.assertEqual(params[5], "2026-09-17T12:00:00")     # datetime → ISO
        self.assertEqual(params[7], 42.5)
        self.assertEqual(params[-1], "k1")

    def test_failure_counts_zero(self):
        with mock.patch.object(db_writer.DB, "execute", side_effect=OSError("db down")):
            self.assertEqual(db_writer.save_global_events([_ev()]), 0)


class RunCalendarTest(unittest.TestCase):
    def test_merge_score_save_health(self):
        now = datetime(2026, 9, 17, 10, 0)
        wscn = [_ev(kind="scheduled", category="central_bank", importance=4, country="美国",
                    source="wscn_calendar", dedup_key="w1", event_time=datetime(2026, 9, 18, 2, 0))]
        ff = [_ev(kind="scheduled", category="jobs", importance=4, country="美国",
                  source="ff_calendar", dedup_key="f1", event_time=datetime(2026, 10, 2, 20, 30))]
        saved = []
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(C, "HEALTH_PATH", os.path.join(d, "h.json")), \
                 mock.patch.object(C.S, "fetch_wscn_calendar", return_value=wscn), \
                 mock.patch.object(C.S, "fetch_ff_calendar", return_value=ff), \
                 mock.patch.object(C.db_writer, "save_global_events", side_effect=lambda evs: saved.extend(evs) or len(evs)):
                n = C.run_calendar(now)
                h = H.load_health(path=os.path.join(d, "h.json"))
        self.assertEqual(n, 2)
        self.assertEqual(saved[0]["heat_base"], 100.0)
        self.assertEqual(saved[0]["heat_score"], 100.0)
        self.assertTrue(h["wscn_calendar"]["ok"] and h["ff_calendar"]["ok"])

    def test_source_failure_recorded_and_others_saved(self):
        now = datetime(2026, 9, 17, 10, 0)
        ff = [_ev(kind="scheduled", source="ff_calendar", dedup_key="f1")]
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(C, "HEALTH_PATH", os.path.join(d, "h.json")), \
                 mock.patch.object(C.S, "fetch_wscn_calendar", side_effect=OSError("timeout")), \
                 mock.patch.object(C.S, "fetch_ff_calendar", return_value=ff), \
                 mock.patch.object(C.db_writer, "save_global_events", return_value=1):
                n = C.run_calendar(now)
                h = H.load_health(path=os.path.join(d, "h.json"))
        self.assertEqual(n, 1)
        self.assertFalse(h["wscn_calendar"]["ok"])
        self.assertEqual(h["wscn_calendar"]["last_error"], "timeout")


class RunBreakingTest(unittest.TestCase):
    def test_scores_with_cooccurrence_and_cls_flag(self):
        now = datetime(2026, 9, 17, 12, 0)
        live = [_ev(title="美联储重启加息25个基点", category="central_bank", importance=3,
                    source="wscn_live", dedup_key="l1", event_time=datetime(2026, 9, 17, 11, 0))]
        gnews_raw = [_ev(title="Fed raises rates", category="central_bank", importance=2, reading_num=1,
                         source="gnews", dedup_key="g1", event_time=datetime(2026, 9, 17, 11, 30))]
        db_rows = [{"title": "时隔三年多！美联储重启加息25个基点", "source": "cls_hot"}]
        saved = []
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(C, "HEALTH_PATH", os.path.join(d, "h.json")), \
                 mock.patch.object(C.config, "GLOBAL_EVENTS_CLS_ENABLED", False), \
                 mock.patch.object(C.config, "GLOBAL_EVENTS_HOT_URL", ""), \
                 mock.patch.object(C.S, "fetch_wscn_live", return_value=live), \
                 mock.patch.object(C.S, "fetch_cls_roll") as cls_roll, \
                 mock.patch.object(C.S, "fetch_cls_hot") as cls_hot, \
                 mock.patch.object(C.S, "fetch_gnews", return_value=gnews_raw), \
                 mock.patch.object(C.db_writer.DB, "query", return_value=db_rows), \
                 mock.patch.object(C.db_writer, "save_global_events", side_effect=lambda evs: saved.extend(evs) or len(evs)):
                n = C.run_breaking(now)
                h = H.load_health(path=os.path.join(d, "h.json"))
        self.assertEqual(n, 2)
        cls_roll.assert_not_called()
        cls_hot.assert_not_called()
        self.assertNotIn("cls_roll", h)
        l = [e for e in saved if e["dedup_key"] == "l1"][0]
        # 50 (imp3) + 15 (与 DB 里 cls_hot 同现) + 10 (央行类) = 75; 1h 衰减 → 75*0.5^(1/3)=59.53
        self.assertEqual(l["heat_base"], 75.0)
        self.assertEqual(l["heat_score"], 59.53)
        g = [e for e in saved if e["dedup_key"] == "g1"][0]
        # 25 (imp2) + 10 (单条批次 reading 分位 0.5×20) + 10 (央行类); 英文不与中文比较, 无同现
        self.assertEqual(g["heat_base"], 45.0)

    def test_all_sources_fail_returns_zero(self):
        now = datetime(2026, 9, 17, 12, 0)
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(C, "HEALTH_PATH", os.path.join(d, "h.json")), \
                 mock.patch.object(C.config, "GLOBAL_EVENTS_CLS_ENABLED", True), \
                 mock.patch.object(C.S, "fetch_wscn_live", side_effect=OSError("a")), \
                 mock.patch.object(C.S, "fetch_cls_roll", side_effect=OSError("b")), \
                 mock.patch.object(C.S, "fetch_cls_hot", side_effect=OSError("c")), \
                 mock.patch.object(C.S, "fetch_gnews", side_effect=OSError("d")), \
                 mock.patch.object(C.db_writer.DB, "query", return_value=[]), \
                 mock.patch.object(C.db_writer, "save_global_events", return_value=0) as sv:
                n = C.run_breaking(now)
                h = H.load_health(path=os.path.join(d, "h.json"))
        self.assertEqual(n, 0)
        sv.assert_not_called()
        self.assertEqual(sorted(k for k, v in h.items() if not v["ok"]), ["cls_hot", "cls_roll", "gnews", "wscn_live"])


class MainTest(unittest.TestCase):
    def test_main_exit_zero_on_failure(self):
        with mock.patch.object(C, "run_calendar", side_effect=RuntimeError("x")):
            self.assertEqual(C.main(["--calendar"]), 0)

    def test_check_reports(self):
        with mock.patch.object(C.S, "fetch_wscn_calendar", return_value=[1, 2]), \
             mock.patch.object(C.S, "fetch_ff_calendar", side_effect=OSError("nope")), \
             mock.patch.object(C.S, "fetch_wscn_live", return_value=[]), \
             mock.patch.object(C.S, "fetch_cls_roll", return_value=[]), \
             mock.patch.object(C.S, "fetch_cls_hot", return_value=[]), \
             mock.patch.object(C.S, "fetch_gnews", return_value=[]), \
             mock.patch.object(C.config, "GLOBAL_EVENTS_HOT_URL", ""):
            r = C.run_check()
        self.assertEqual(r["wscn_calendar"], (True, 2, None))
        self.assertEqual(r["ff_calendar"][0], False)
        self.assertEqual(r["dailyhot"], (None, 0, "disabled"))


if __name__ == "__main__":
    unittest.main()
