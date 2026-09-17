# -*- coding: utf-8 -*-
import unittest
from datetime import datetime, timedelta

from global_events import scoring as SC


class SurpriseTest(unittest.TestCase):
    def test_pct(self):
        self.assertAlmostEqual(SC.surprise_pct("2.0%", "2.2%"), 10.0)
        self.assertAlmostEqual(SC.surprise_pct("20.7", "21.0"), 1.449, places=2)

    def test_none_cases(self):
        self.assertIsNone(SC.surprise_pct(None, "1"))
        self.assertIsNone(SC.surprise_pct("0", "1"))
        self.assertIsNone(SC.surprise_pct("abc", "1"))
        self.assertIsNone(SC.surprise_pct("1", None))


class HeatScheduledTest(unittest.TestCase):
    def test_fomc_us(self):
        ev = {"category": "central_bank", "importance": 4, "country": "美国", "expected": None, "actual": None}
        self.assertEqual(SC.heat_scheduled(ev), 100.0)     # 85+10+5 → clamp 100

    def test_low_other(self):
        ev = {"category": "other", "importance": 1, "country": "瑞士"}
        self.assertEqual(SC.heat_scheduled(ev), 10.0)

    def test_surprise_bonus(self):
        ev = {"category": "growth", "importance": 2, "country": "欧元区", "expected": "50.0", "actual": "53.0"}
        self.assertEqual(SC.heat_scheduled(ev), 45.0)     # 30 + 15
        ev["actual"] = "50.5"
        self.assertEqual(SC.heat_scheduled(ev), 30.0)


class PercentileTest(unittest.TestCase):
    def test_rank(self):
        self.assertEqual(SC.percentile_rank([10, 20, 30, 40, 50], 50), 1.0)
        self.assertEqual(SC.percentile_rank([10, 20, 30, 40, 50], 10), 0.0)
        self.assertEqual(SC.percentile_rank([10, 20, 30, 40, 50], 30), 0.5)
        self.assertEqual(SC.percentile_rank([7], 7), 0.5)
        self.assertEqual(SC.percentile_rank([], 7), 0.5)


class CooccurrenceTest(unittest.TestCase):
    def test_same_lang_other_source(self):
        others = [("美联储重启加息25个基点", "cls_hot")]
        # similarity = 2*12/(17+12) ≈ 0.83 ≥ 0.6
        self.assertTrue(SC.has_cooccurrence("时隔三年多！美联储重启加息25个基点", "wscn_live", others))

    def test_below_threshold(self):
        others = [("美联储重启加息25个基点 内部预计年内还会升息一次", "cls_hot")]
        # similarity = 2*12/(17+26) ≈ 0.56 < 0.6
        self.assertFalse(SC.has_cooccurrence("时隔三年多！美联储重启加息25个基点", "wscn_live", others))

    def test_same_source_ignored(self):
        others = [("美联储重启加息25个基点", "wscn_live")]
        self.assertFalse(SC.has_cooccurrence("美联储重启加息25个基点", "wscn_live", others))

    def test_cross_language_not_compared(self):
        others = [("Fed raises interest rates", "gnews")]
        self.assertFalse(SC.has_cooccurrence("美联储加息", "wscn_live", others))


class SocialTest(unittest.TestCase):
    def test_bigrams(self):
        self.assertEqual(SC.cjk_bigrams("美联储加息"), {"美联", "联储", "储加", "加息"})

    def test_hit_needs_three_shared(self):
        self.assertTrue(SC.social_hit("美联储宣布加息25个基点", ["美联储加息了"]))       # 美联/联储/加息
        self.assertFalse(SC.social_hit("市场今日大涨", ["美国市场"]))                 # 只共享 市场


class HeatBreakingTest(unittest.TestCase):
    def _ev(self, **kw):
        base = {"title": "美联储重启加息25个基点", "category": "central_bank", "importance": 4,
                "reading_num": 50000, "tickers": None, "source": "cls_hot"}
        base.update(kw)
        return base

    def test_full_stack(self):
        ev = self._ev(title="英伟达财报后美联储加息25个基点")
        batch = [100, 1000, 50000]
        others = [("美联储加息25个基点", "wscn_live")]
        hot = ["美联储加息引发热议"]
        # 70 + 20*1.0 + 15 + 10 + 10 + 15 = 140 → 100
        self.assertEqual(SC.heat_breaking_base(ev, batch, others, hot), 100.0)

    def test_minimal(self):
        ev = self._ev(title="某公司发布公告", category="other", importance=1, reading_num=None)
        self.assertEqual(SC.heat_breaking_base(ev, [], [], []), 8.0)

    def test_reading_percentile(self):
        ev = self._ev(category="other", importance=2, reading_num=30)
        self.assertEqual(SC.heat_breaking_base(ev, [10, 20, 30, 40, 50], [], []), 35.0)   # 25 + 10


class DecayTest(unittest.TestCase):
    def test_half_life(self):
        t0 = datetime(2026, 9, 17, 12, 0)
        self.assertEqual(SC.apply_decay(80.0, t0, t0), 80.0)
        self.assertEqual(SC.apply_decay(80.0, t0, t0 + timedelta(hours=3)), 40.0)
        self.assertEqual(SC.apply_decay(80.0, t0, t0 + timedelta(hours=6)), 20.0)

    def test_future_event_no_decay(self):
        t0 = datetime(2026, 9, 17, 12, 0)
        self.assertEqual(SC.apply_decay(80.0, t0 + timedelta(hours=2), t0), 80.0)


if __name__ == "__main__":
    unittest.main()
