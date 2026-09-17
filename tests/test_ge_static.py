# -*- coding: utf-8 -*-
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "global_events", "static")


class StaticDataTest(unittest.TestCase):
    def test_mega_caps_has_required_tickers(self):
        with open(os.path.join(STATIC, "mega_caps.json"), encoding="utf-8") as f:
            mega = json.load(f)
        for t in ("NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "TSM", "BRK.B"):
            self.assertIn(t, mega)
            self.assertIn("name", mega[t])
            self.assertIsInstance(mega[t]["aliases"], list)

    def test_cn_policy_events_shape(self):
        with open(os.path.join(STATIC, "cn_policy_events.json"), encoding="utf-8") as f:
            rows = json.load(f)
        self.assertIsInstance(rows, list)
        for r in rows:
            self.assertRegex(r["date"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertIn(r["importance"], (1, 2, 3, 4))
            self.assertTrue(r["title"])

    def test_ff_title_map_has_fomc(self):
        with open(os.path.join(STATIC, "ff_title_map.json"), encoding="utf-8") as f:
            m = json.load(f)
        self.assertEqual(m["Federal Funds Rate"], "美联储利率决议")
        self.assertEqual(m["Non-Farm Employment Change"], "非农就业人数变动")

    def test_schema_has_event_global(self):
        with open(os.path.join(ROOT, "scripts", "schema.sql"), encoding="utf-8") as f:
            sql = f.read()
        self.assertIn("CREATE TABLE IF NOT EXISTS `event_global`", sql)
        self.assertIn("`heat_base`", sql)
        self.assertIn("UNIQUE KEY `uk_global_dedup` (`dedup_key`)", sql)

    def test_config_flags(self):
        import config
        self.assertIsInstance(config.GLOBAL_EVENTS_CLS_ENABLED, bool)
        self.assertIsInstance(config.GLOBAL_EVENTS_HOT_URL, str)

    def test_fixtures_present(self):
        for name in ("wscn_calendar.json", "ff_calendar.json", "wscn_live.json",
                     "cls_roll.json", "cls_hot.json", "gnews.xml"):
            self.assertTrue(os.path.exists(os.path.join(ROOT, "tests", "fixtures", name)), name)


if __name__ == "__main__":
    unittest.main()
