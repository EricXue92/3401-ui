# -*- coding: utf-8 -*-
import unittest

from global_events.classify import (categorize, normalize_importance, boost_importance,
                                    mega_tickers_in, CATEGORIES)


class CategorizeTest(unittest.TestCase):
    def test_central_bank_cn_en(self):
        self.assertEqual(categorize("时隔三年多！美联储重启加息25个基点"), "central_bank")
        self.assertEqual(categorize("Fed raises interest rates by quarter point"), "central_bank")

    def test_central_bank_ff_titles(self):
        self.assertEqual(categorize("MPC Official Bank Rate Votes"), "central_bank")
        self.assertEqual(categorize("Monetary Policy Summary"), "central_bank")

    def test_ff_wage_and_claimant_are_jobs_not_earnings(self):
        self.assertEqual(categorize("Average Earnings Index 3m/y"), "jobs")
        self.assertEqual(categorize("Claimant Count Change"), "jobs")
        self.assertEqual(categorize("RBA Gov Bullock Speaks"), "central_bank")

    def test_inflation(self):
        self.assertEqual(categorize("8月核心调和CPI同比终值"), "inflation")
        self.assertEqual(categorize("US inflation cools in August"), "inflation")

    def test_jobs(self):
        self.assertEqual(categorize("9月非农就业人口变动(万人)"), "jobs")
        self.assertEqual(categorize("Weekly jobless claims fall"), "jobs")

    def test_growth(self):
        self.assertEqual(categorize("9月官方制造业PMI"), "growth")
        self.assertEqual(categorize("二季度实际GDP年化季环比终值"), "growth")

    def test_cn_policy(self):
        self.assertEqual(categorize("中央政治局会议分析研究经济形势"), "cn_policy")
        self.assertEqual(categorize("中国央行公开市场今日净投放1590亿元"), "cn_policy")

    def test_earnings(self):
        self.assertEqual(categorize("美光Q4财报电话会"), "earnings")
        self.assertEqual(categorize("Nvidia earnings beat estimates"), "earnings")

    def test_geopolitics(self):
        self.assertEqual(categorize("美国宣布对华加征关税"), "geopolitics")
        self.assertEqual(categorize("Israel ceasefire talks resume"), "geopolitics")

    def test_energy_supply(self):
        self.assertEqual(categorize("沙特加速修复输油管道"), "energy_supply")
        self.assertEqual(categorize("OPEC+ agrees output cut"), "energy_supply")

    def test_fin_risk(self):
        self.assertEqual(categorize("某地产公司美元债违约"), "fin_risk")
        self.assertEqual(categorize("Regional bank files for bankruptcy"), "fin_risk")

    def test_tech_event(self):
        self.assertEqual(categorize("华为全联接大会 2026（上海）"), "tech_event")
        self.assertEqual(categorize("iPhone 18 Pro将于9月18日发售"), "tech_event")

    def test_other_and_word_boundary(self):
        self.assertEqual(categorize("美国8月营建许可年化总数初值(万户)"), "other")
        # "oil" 不应从 "boil" 命中
        self.assertEqual(categorize("Water boils at 100 degrees"), "other")

    def test_connect_word_no_longer_false_positive(self):
        # "Connect" 单词太宽泛, 改为要求 "Meta Connect" 完整短语
        self.assertEqual(categorize("Please connect the cable"), "other")

    def test_cn_policy_npc(self):
        self.assertEqual(categorize("十四届全国人大常委会第十次会议举行"), "cn_policy")

    def test_mega_ticker_without_keyword_is_earnings(self):
        self.assertEqual(categorize("Broadcom announces new chip", tickers="AVGO"), "earnings")

    def test_priority_central_bank_over_inflation(self):
        self.assertEqual(categorize("美联储称通胀过高"), "central_bank")

    def test_categories_list(self):
        self.assertIn("other", CATEGORIES)
        self.assertEqual(len(CATEGORIES), 11)


class ImportanceTest(unittest.TestCase):
    def test_wscn_calendar(self):
        self.assertEqual(normalize_importance("wscn_calendar", 4), 4)
        self.assertEqual(normalize_importance("wscn_calendar", "2"), 2)
        self.assertEqual(normalize_importance("wscn_calendar", None), 1)
        self.assertEqual(normalize_importance("wscn_calendar", 9), 4)

    def test_ff(self):
        self.assertEqual(normalize_importance("ff_calendar", "High"), 4)
        self.assertEqual(normalize_importance("ff_calendar", "Medium"), 2)
        self.assertEqual(normalize_importance("ff_calendar", "Low"), 1)

    def test_wscn_live(self):
        self.assertEqual(normalize_importance("wscn_live", 2), 3)
        self.assertEqual(normalize_importance("wscn_live", 1), 1)

    def test_cls(self):
        self.assertEqual(normalize_importance("cls_roll", "A"), 4)
        self.assertEqual(normalize_importance("cls_roll", "B"), 3)
        self.assertEqual(normalize_importance("cls_roll", "C"), 2)
        self.assertEqual(normalize_importance("cls_hot", 0), 4)
        self.assertEqual(normalize_importance("cls_hot", 5), 3)

    def test_gnews_cluster_size(self):
        self.assertEqual(normalize_importance("gnews", 1), 2)
        self.assertEqual(normalize_importance("gnews", 10), 3)
        self.assertEqual(normalize_importance("gnews", 30), 4)

    def test_unknown_source(self):
        self.assertEqual(normalize_importance("whatever", "x"), 1)


class BoostTest(unittest.TestCase):
    def test_core_us_macro_boosted(self):
        self.assertEqual(boost_importance("9月非农就业人口变动(万人)", "美国", 3), 4)
        self.assertEqual(boost_importance("8月CPI同比", "美国", 3), 4)
        self.assertEqual(boost_importance("8月核心PCE物价指数同比", "美国", 2), 4)
        self.assertEqual(boost_importance("Federal Funds Rate", "USD", 4), 4)

    def test_non_us_cpi_not_boosted(self):
        self.assertEqual(boost_importance("8月核心调和CPI同比终值", "欧元区", 2), 2)

    def test_china_core(self):
        self.assertEqual(boost_importance("9月官方制造业PMI", "中国", 3), 4)
        self.assertEqual(boost_importance("9月一年期贷款市场报价利率(LPR)", "中国", 3), 4)
        self.assertEqual(boost_importance("9月RatingDog制造业PMI", "中国", 3), 3)

    def test_central_bank_decisions(self):
        self.assertEqual(boost_importance("日本央行9月议息会议", "日本", 4), 4)
        self.assertEqual(boost_importance("英国央行政策利率", "英国", 3), 4)
        self.assertEqual(boost_importance("欧洲央行管委雷恩发表讲话", "英国", 1), 1)   # 讲话不提升

    def test_speeches_and_subitems_not_boosted(self):
        self.assertEqual(boost_importance("2027年FOMC票委、芝加哥联储主席古尔斯比发表讲话。", "美国", 1), 1)
        self.assertEqual(boost_importance("FOMC会议纪要", "美国", 3), 3)
        self.assertEqual(boost_importance("8月实际个人消费支出(PCE)环比", "美国", 2), 2)
        self.assertEqual(boost_importance("二季度GDP平减指数年化季环比终值", "美国", 2), 2)
        self.assertEqual(boost_importance("二季度实际GDP年化季环比终值", "美国", 3), 3)
        self.assertEqual(boost_importance("二季度实际GDP年化季环比初值", "美国", 3), 4)
        self.assertEqual(boost_importance("8月核心PCE物价指数同比", "美国", 2), 4)

    def test_tech_event_unchanged(self):
        self.assertEqual(boost_importance("华为全联接大会 2026（上海）", "中国", 4), 4)

    def test_election_is_geopolitics(self):
        self.assertEqual(categorize("美国 11 月 3 日中期选举"), "geopolitics")


class MegaTest(unittest.TestCase):
    def test_by_ticker_string(self):
        self.assertEqual(mega_tickers_in("无关标题", "NVDA,XYZ"), ["NVDA"])

    def test_by_alias_cn(self):
        self.assertEqual(mega_tickers_in("英伟达GTC大会（柏林）"), ["NVDA"])

    def test_by_alias_en_word_boundary(self):
        self.assertEqual(mega_tickers_in("Apple unveils new Mac"), ["AAPL"])
        self.assertEqual(mega_tickers_in("Metaverse hype fades"), [])

    def test_none(self):
        self.assertEqual(mega_tickers_in("欧元区CPI", None), [])

    def test_by_ticker_string_brk_b(self):
        self.assertEqual(mega_tickers_in("x", "BRK-B"), ["BRK-B"])


if __name__ == "__main__":
    unittest.main()
