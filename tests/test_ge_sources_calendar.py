# -*- coding: utf-8 -*-
import json
import os
import unittest
from datetime import datetime
from unittest import mock

from global_events import sources as S

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _load(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return json.load(f)


class TimeHelpersTest(unittest.TestCase):
    def test_ts_to_hkt(self):
        self.assertEqual(S.ts_to_hkt(1789635600), datetime(2026, 9, 17, 17, 0, 0))

    def test_iso_with_offset(self):
        self.assertEqual(S.iso_to_hkt("2026-09-14T08:30:00-04:00"), datetime(2026, 9, 14, 20, 30, 0))

    def test_rfc822(self):
        self.assertEqual(S.rfc822_to_hkt("Wed, 16 Sep 2026 23:42:00 GMT"), datetime(2026, 9, 17, 7, 42, 0))

    def test_dedup_key_stable(self):
        self.assertEqual(S.make_dedup_key("a", 1), S.make_dedup_key("a", "1"))
        self.assertEqual(len(S.make_dedup_key("x")), 40)


class ClsSignTest(unittest.TestCase):
    def test_sign_matches_verified_value(self):
        # 由 newsnow/RSSHub 规则: sign = md5(sha1(sorted query))。以下值用固定参数实算并核对。
        qs = S.cls_signed_query({"rn": "30", "lastTime": "1789600000"})
        self.assertTrue(qs.startswith("app=CailianpressWeb&lastTime=1789600000&os=web&rn=30&sv=7.7.5&sign="))
        import hashlib
        base = "app=CailianpressWeb&lastTime=1789600000&os=web&rn=30&sv=7.7.5"
        expect = hashlib.md5(hashlib.sha1(base.encode()).hexdigest().encode()).hexdigest()
        self.assertTrue(qs.endswith("sign=" + expect))


class WscnCalendarParseTest(unittest.TestCase):
    def test_parse(self):
        evs = S.parse_wscn_calendar(_load("wscn_calendar.json"))
        self.assertEqual(len(evs), 2)
        e = evs[0]
        self.assertEqual(e["kind"], "scheduled")
        self.assertEqual(e["source"], "wscn_calendar")
        self.assertEqual(e["title"], "8月核心调和CPI同比终值")
        self.assertEqual(e["category"], "inflation")
        self.assertEqual(e["country"], "欧元区")
        self.assertEqual(e["event_time"], datetime(2026, 9, 17, 17, 0, 0))
        self.assertEqual(e["importance"], 2)
        self.assertEqual(e["expected"], "2.4")
        self.assertEqual(e["previous"], "2.4")
        self.assertIsNone(e["actual"])          # "" → None
        self.assertEqual(e["source_id"], "1591949")
        self.assertEqual(e["url"], "https://wallstreetcn.com/calendar/EA111955/overview")
        self.assertEqual(e["dedup_key"], S.make_dedup_key("wscn_calendar", 1591949))
        h = evs[1]
        self.assertEqual(h["category"], "tech_event")
        self.assertEqual(h["importance"], 4)
        self.assertIsNone(h["url"])              # "" → None

    def test_empty_payload(self):
        self.assertEqual(S.parse_wscn_calendar({"code": 60502, "data": {}}), [])


class FfCalendarParseTest(unittest.TestCase):
    def test_parse_skips_low_and_translates(self):
        evs = S.parse_ff_calendar(_load("ff_calendar.json"))
        self.assertEqual(len(evs), 2)               # Low 被丢弃
        e = evs[0]
        self.assertEqual(e["source"], "ff_calendar")
        self.assertEqual(e["title"], "CPI 环比")
        self.assertEqual(e["country"], "加拿大")
        self.assertEqual(e["category"], "inflation")
        self.assertEqual(e["importance"], 4)
        self.assertEqual(e["event_time"], datetime(2026, 9, 14, 20, 30, 0))
        self.assertEqual(e["expected"], "-0.1%")
        self.assertEqual(e["dedup_key"], S.make_dedup_key("ff_calendar", "CAD", "CPI m/m", "2026-09-14T08:30:00-04:00"))

    def test_unmapped_title_kept_english(self):
        evs = S.parse_ff_calendar([{"title": "Some Odd Event", "country": "USD", "date": "2026-09-14T08:30:00-04:00",
                                    "impact": "Medium", "forecast": "", "previous": ""}])
        self.assertEqual(evs[0]["title"], "Some Odd Event")
        self.assertEqual(evs[0]["country"], "美国")
        self.assertEqual(evs[0]["importance"], 2)

    def test_bad_item_skipped_good_item_kept(self):
        bad = {"title": "X", "country": "USD", "date": "not-a-date", "impact": "High"}
        good = {
            "title": "CPI m/m",
            "country": "CAD",
            "date": "2026-09-14T08:30:00-04:00",
            "impact": "High",
            "forecast": "-0.1%",
            "previous": "0.5%",
        }
        evs = S.parse_ff_calendar([bad, good])
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["title"], "CPI 环比")


class ChunkTest(unittest.TestCase):
    def test_14_days_two_chunks_of_7(self):
        start = datetime(2026, 9, 17, 0, 0, 0)
        chunks = S.calendar_chunks(start, days=14, chunk_days=7)
        self.assertEqual(len(chunks), 2)
        for a, b in chunks:
            self.assertLessEqual(b - a, 7 * 86400)
        self.assertEqual(chunks[1][0], chunks[0][1])

    def test_fetch_wscn_calendar_partial_failure(self):
        payload = _load("wscn_calendar.json")
        calls = {"n": 0}

        def fake(url, headers=None):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("timeout")
            return payload

        with mock.patch.object(S, "http_json", side_effect=fake):
            evs = S.fetch_wscn_calendar(datetime(2026, 9, 17), days=14)
        self.assertEqual(len(evs), 2)   # 第一段成功, 第二段失败 → 返回部分

    def test_fetch_wscn_calendar_all_fail_raises(self):
        with mock.patch.object(S, "http_json", side_effect=OSError("down")):
            with self.assertRaises(OSError):
                S.fetch_wscn_calendar(datetime(2026, 9, 17), days=14)

    def test_fetch_ff_nextweek_optional(self):
        items = _load("ff_calendar.json")

        def fake(url, headers=None):
            if "nextweek" in url:
                raise OSError("404")
            return items

        with mock.patch.object(S, "http_json", side_effect=fake):
            evs = S.fetch_ff_calendar()
        self.assertEqual(len(evs), 2)


if __name__ == "__main__":
    unittest.main()
