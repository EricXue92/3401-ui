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


def _text(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return f.read()


class WscnLiveTest(unittest.TestCase):
    def test_parse(self):
        evs = S.parse_wscn_live(_load("wscn_live.json"))
        self.assertEqual(len(evs), 2)
        e = evs[0]
        self.assertEqual(e["kind"], "breaking")
        self.assertEqual(e["source"], "wscn_live")
        self.assertTrue(e["title"].startswith("创业板指涨超1%"))   # 无 title 时用正文前 80 字
        self.assertLessEqual(len(e["title"]), 80)
        self.assertEqual(e["summary"][:10], "创业板指涨超1%，沪")
        self.assertEqual(e["event_time"], datetime(2026, 9, 17, 9, 37, 30))
        self.assertEqual(e["importance"], 3)          # score 2
        self.assertEqual(e["url"], "https://wallstreetcn.com/livenews/3166594")
        self.assertEqual(e["dedup_key"], S.make_dedup_key("wscn_live", 3166594))
        self.assertEqual(evs[1]["importance"], 1)
        self.assertEqual(evs[1]["category"], "central_bank")   # 日本央行

    def test_fetch_url(self):
        with mock.patch.object(S, "http_json", return_value={"data": {"items": []}}) as m:
            S.fetch_wscn_live(limit=7)
        self.assertIn("channel=global-channel", m.call_args[0][0])
        self.assertIn("limit=7", m.call_args[0][0])


class ClsRollTest(unittest.TestCase):
    def test_parse(self):
        evs = S.parse_cls_roll(_load("cls_roll.json"))
        self.assertEqual(len(evs), 2)
        e = evs[0]
        self.assertEqual(e["source"], "cls_roll")
        self.assertEqual(e["title"], "午评：三大指数集体冲高回落 汽车、医药股走强")
        self.assertEqual(e["importance"], 3)          # level B
        self.assertEqual(e["reading_num"], 47154)
        self.assertEqual(e["event_time"], datetime(2026, 9, 17, 11, 32, 20))
        self.assertTrue(e["url"].startswith("https://api3.cls.cn/share/article/2485865"))
        self.assertEqual(e["dedup_key"], S.make_dedup_key("cls_roll", 2485865))
        # 无 title 用 brief 去掉 "财联社X月X日电，" 前缀
        self.assertTrue(evs[1]["title"].startswith("2年期日本国债收益率"))
        self.assertEqual(evs[1]["importance"], 2)

    def test_bad_ctime_skipped_good_item_kept(self):
        bad = {"id": 1, "title": "坏数据", "ctime": "abc"}
        good = {"id": 2, "title": "美联储重启加息25个基点", "ctime": 1789635600, "level": "B"}
        evs = S.parse_cls_roll({"data": {"roll_data": [bad, good]}})
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["title"], "美联储重启加息25个基点")

    def test_fetch_signed(self):
        with mock.patch.object(S, "http_json", return_value={"data": {"roll_data": []}}) as m:
            S.fetch_cls_roll(rn=20, now_ts=1789600000)
        url, = m.call_args[0]
        self.assertTrue(url.startswith(S.CLS_ROLL_URL + "?"))
        self.assertIn("rn=20", url)
        self.assertIn("lastTime=1789600000", url)
        self.assertIn("&sign=", url)
        self.assertEqual(m.call_args[1]["headers"], S.CLS_HEADERS)


class ClsHotTest(unittest.TestCase):
    def test_parse(self):
        evs = S.parse_cls_hot(_load("cls_hot.json"))
        self.assertEqual(len(evs), 2)
        self.assertEqual(evs[0]["importance"], 4)     # 榜首
        self.assertEqual(evs[1]["importance"], 3)
        self.assertEqual(evs[0]["reading_num"], 234263)
        self.assertEqual(evs[0]["category"], "central_bank")
        self.assertEqual(evs[0]["url"], "https://www.cls.cn/detail/2485507")
        self.assertEqual(evs[0]["event_time"], datetime(2026, 9, 17, 2, 0, 19))
        self.assertIsNone(evs[0]["tickers"])          # "" → None
        self.assertEqual(evs[0]["dedup_key"], S.make_dedup_key("cls_hot", 2485507))


class GnewsTest(unittest.TestCase):
    def test_parse(self):
        evs = S.parse_gnews(_text("gnews.xml"), "central_bank")
        self.assertEqual(len(evs), 3)
        e = evs[0]
        self.assertEqual(e["source"], "gnews")
        self.assertEqual(e["title"], "Federal Reserve hikes key rate for 1st time in 3 years, defying Trump demands for a cut")
        self.assertEqual(e["summary"], "AP News")
        self.assertEqual(e["category"], "central_bank")
        self.assertEqual(e["importance"], 2)
        self.assertEqual(e["reading_num"], 1)
        self.assertEqual(e["event_time"], datetime(2026, 9, 17, 7, 42, 0))
        self.assertTrue(e["url"].startswith("https://news.google.com/rss/articles/"))
        import re as _re
        guid = _re.search(r"<guid[^>]*>(.*?)</guid>", _text("gnews.xml")).group(1)
        self.assertEqual(e["dedup_key"], S.make_dedup_key("gnews", guid))

    def test_hint_used_when_other(self):
        xml = _text("gnews.xml").replace("Federal Reserve hikes key rate for 1st time in 3 years, defying Trump demands for a cut", "Something happened today")
        evs = S.parse_gnews(xml, "geopolitics")
        self.assertEqual(evs[0]["category"], "geopolitics")

    def test_fetch_tolerates_partial(self):
        xml = _text("gnews.xml")
        calls = {"n": 0}

        def fake(url, headers=None, timeout=10):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("boom")
            return xml.encode("utf-8")

        with mock.patch.object(S, "http_get", side_effect=fake):
            evs = S.fetch_gnews()
        self.assertEqual(calls["n"], len(S.GNEWS_QUERIES))
        self.assertEqual(len(evs), 3 * (len(S.GNEWS_QUERIES) - 1))

    def test_fetch_all_fail_raises(self):
        with mock.patch.object(S, "http_get", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                S.fetch_gnews()


class DailyHotTest(unittest.TestCase):
    def test_titles_merged_and_tolerant(self):
        def fake(url, headers=None):
            if url.endswith("/weibo"):
                return {"code": 200, "data": [{"title": "美联储加息"}, {"title": "某明星"}]}
            if url.endswith("/baidu"):
                raise OSError("down")
            return {"code": 200, "data": [{"title": "关税"}]}

        with mock.patch.object(S, "http_json", side_effect=fake):
            titles = S.fetch_dailyhot("http://127.0.0.1:6688")
        self.assertEqual(titles, ["美联储加息", "某明星", "关税"])

    def test_empty_url(self):
        self.assertEqual(S.fetch_dailyhot(""), [])


if __name__ == "__main__":
    unittest.main()
