# -*- coding: utf-8 -*-
import unittest
from datetime import datetime, timedelta

from global_events.merge import similarity, merge_ff_into_wscn, cluster_gnews


def ev(**kw):
    base = {"kind": "scheduled", "category": "other", "title": "", "summary": None, "country": None,
            "event_time": datetime(2026, 9, 17, 20, 30), "importance": 2, "heat_base": 0.0, "heat_score": 0.0,
            "reading_num": None, "expected": None, "previous": None, "actual": None, "tickers": None,
            "source": "x", "source_id": None, "url": None, "dedup_key": "k"}
    base.update(kw)
    return base


class SimilarityTest(unittest.TestCase):
    def test_case_insensitive(self):
        self.assertEqual(similarity("Fed Hikes", "fed hikes"), 1.0)

    def test_different(self):
        self.assertLess(similarity("美联储加息", "沙特管道修复"), 0.4)


class MergeFfTest(unittest.TestCase):
    def test_duplicate_dropped(self):
        w = [ev(title="美联储利率决议", country="美国", category="central_bank",
                event_time=datetime(2026, 9, 17, 2, 0), source="wscn_calendar")]
        f = [ev(title="美联储利率决议", country="美国", category="central_bank",
                event_time=datetime(2026, 9, 17, 2, 0), source="ff_calendar", importance=4)]
        out = merge_ff_into_wscn(w, f)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["source"], "wscn_calendar")

    def test_gap_filled(self):
        w = [ev(title="8月CPI", country="美国", category="inflation", event_time=datetime(2026, 9, 17, 20, 30))]
        f = [ev(title="非农就业人数变动", country="美国", category="jobs",
                event_time=datetime(2026, 10, 2, 20, 30), source="ff_calendar", importance=4)]
        out = merge_ff_into_wscn(w, f)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[1]["source"], "ff_calendar")

    def test_central_bank_same_settlement_day_is_duplicate(self):
        w = [ev(title="日本央行9月议息会议", country="日本", category="central_bank", event_time=datetime(2026, 9, 18, 12, 2))]
        f = [ev(title="日本央行利率决议", country="日本", category="central_bank", event_time=datetime(2026, 9, 18, 10, 30), source="ff_calendar", importance=4),
             ev(title="日本央行利率决议", country="日本", category="central_bank", event_time=datetime(2026, 9, 19, 10, 30), source="ff_calendar", importance=4)]
        out = merge_ff_into_wscn(w, f)
        self.assertEqual(len(out), 2)                       # 同交割日的被丢弃, 次日的保留
        self.assertEqual(out[1]["event_time"], datetime(2026, 9, 19, 10, 30))

    def test_window_boundary(self):
        w = [ev(title="CPI 环比", country="加拿大", category="inflation", event_time=datetime(2026, 9, 14, 20, 30))]
        near = ev(title="CPI m/m", country="加拿大", category="inflation",
                  event_time=datetime(2026, 9, 14, 21, 0), source="ff_calendar")
        far = ev(title="CPI m/m", country="加拿大", category="inflation",
                 event_time=datetime(2026, 9, 14, 21, 1), source="ff_calendar")
        self.assertEqual(len(merge_ff_into_wscn(w, [near])), 1)
        self.assertEqual(len(merge_ff_into_wscn(w, [far])), 2)


class ClusterGnewsTest(unittest.TestCase):
    def _items(self):
        t0 = datetime(2026, 9, 17, 7, 0)
        return [
            ev(kind="breaking", source="gnews", title="Fed raises interest rates by a quarter point",
               event_time=t0 + timedelta(minutes=30), reading_num=1, dedup_key="a"),
            ev(kind="breaking", source="gnews", title="Fed raises interest rates by quarter point to tackle inflation",
               event_time=t0, reading_num=1, dedup_key="b"),
            ev(kind="breaking", source="gnews", title="OPEC agrees surprise output cut",
               event_time=t0 + timedelta(minutes=5), reading_num=1, dedup_key="c"),
        ]

    def test_similar_titles_merge_keep_earliest(self):
        out = cluster_gnews(self._items())
        self.assertEqual(len(out), 2)
        fed = [x for x in out if "Fed" in x["title"]][0]
        self.assertEqual(fed["reading_num"], 2)
        self.assertEqual(fed["event_time"], datetime(2026, 9, 17, 7, 0))
        self.assertEqual(fed["dedup_key"], "b")     # 簇代表 = 最早一条
        self.assertEqual(fed["importance"], 2)

    def test_importance_scales_with_cluster(self):
        items = [ev(kind="breaking", source="gnews", title="Fed raises rates %d" % i,
                    event_time=datetime(2026, 9, 17, 7, i), reading_num=1, dedup_key=str(i)) for i in range(12)]
        out = cluster_gnews(items)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["reading_num"], 12)
        self.assertEqual(out[0]["importance"], 3)

    def test_empty(self):
        self.assertEqual(cluster_gnews([]), [])


if __name__ == "__main__":
    unittest.main()
