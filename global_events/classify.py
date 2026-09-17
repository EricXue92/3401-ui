# -*- coding: utf-8 -*-
"""全球重大事件 — 分类与重要性归一化 (纯函数, 无网络无 DB)。"""
import json
import os
import re

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def load_static(name):
    """读取 global_events/static/<name> JSON。"""
    with open(os.path.join(_STATIC_DIR, name), "r", encoding="utf-8") as f:
        return json.load(f)


MEGA_CAPS = load_static("mega_caps.json")

# 顺序即优先级: 先命中者胜。ASCII 关键词按单词边界匹配 (不区分大小写), 中文按子串。
CATEGORY_RULES = [
    ("cn_policy", ["政治局", "国务院", "发改委", "两会", "中央经济工作会议", "公开市场", "中国央行", "人民银行",
                  "人大", "全国人民代表大会"]),
    ("central_bank", ["联储", "FOMC", "央行", "议息", "政策利率", "LPR", "加息", "降息", "缩表",
                      "鲍威尔", "沃什", "植田", "拉加德",
                      "Fed", "Federal Reserve", "rate decision", "ECB", "BOJ", "Bank of England",
                      "interest rate", "interest rates", "Monetary Policy", "Bank Rate", "Policy Rate", "MPC", "RBA", "RBNZ", "SNB", "Fed Chair"]),
    ("inflation", ["CPI", "PCE", "PPI", "通胀", "inflation"]),
    ("jobs", ["非农", "失业", "ADP", "就业", "payroll", "payrolls", "jobless", "unemployment",
              "Claimant Count", "Average Earnings"]),
    ("growth", ["GDP", "PMI", "零售销售", "工业产出", "消费者信心", "retail sales"]),
    ("earnings", ["财报", "业绩", "电话会", "指引", "earnings", "guidance"]),
    ("geopolitics", ["冲突", "袭击", "空袭", "停火", "制裁", "关税", "战争", "封锁", "导弹",
                     "选举", "大选", "爆炸", "军事", "宣战", "开战", "特朗普", "白宫",
                     "Trump", "White House", "Truth Social", "ceasefire", "strike", "strikes", "sanction", "sanctions", "tariff", "tariffs", "election",
                     "missile", "invasion", "war"]),
    ("energy_supply", ["原油", "OPEC", "管道", "航运", "停产", "天然气", "港口",
                       "oil", "pipeline", "shipping", "natural gas", "output cut"]),
    ("fin_risk", ["违约", "破产", "流动性", "挤兑", "评级下调", "暴跌", "熔断",
                  "default", "bankruptcy", "downgrade", "bank run"]),
    ("tech_event", ["大会", "发布会", "发售", "GTC", "DevDay", "Meta Connect", "keynote", "WWDC"]),
]
CATEGORIES = [c for c, _ in CATEGORY_RULES] + ["other"]


def _kw_hit(text, kw):
    """ASCII 关键词: 不区分大小写 + 字母边界; 含非 ASCII 的关键词: 子串。"""
    if kw.isascii():
        pat = r"(?<![A-Za-z])" + re.escape(kw) + r"(?![A-Za-z])"
        return re.search(pat, text, re.IGNORECASE) is not None
    return kw in text


def categorize(title, tickers=None):
    """标题 → category。无关键词命中但 tickers/别名命中巨头 → earnings, 否则 other。"""
    text = title or ""
    for cat, kws in CATEGORY_RULES:
        for kw in kws:
            if _kw_hit(text, kw):
                return cat
    if mega_tickers_in(text, tickers):
        return "earnings"
    return "other"


# 核心宏观事件: 无论来源打几星, 一律提升到 4 星 (用户要求只看 4 星及以上, 但见闻把非农/CPI 标 3 星)。
# 只提升"主指标": 讲话/出席/纪要/票委/子项 (环比初值修正、消费支出金额等) 不提升。
# (国家集合或 None=不限, 标题正则)
KEY_MACRO_PATTERNS = [
    ({"美国", "USD"}, re.compile(r"非农就业人口|失业率|Non-?Farm Employment|Unemployment Rate", re.I)),
    ({"美国", "USD"}, re.compile(r"(?<![A-Za-z])(核心)?CPI(同比|环比| m/m| y/y)", re.I)),
    ({"美国", "USD"}, re.compile(r"PCE物价指数(同比|环比)|Core PCE Price Index", re.I)),
    ({"美国", "USD"}, re.compile(r"实际GDP年化季环比初值|Advance GDP", re.I)),
    ({"美国", "USD"}, re.compile(r"ISM(制造业|非制造业|服务业)PMI|ISM (Manufacturing|Services) PMI", re.I)),
    (None, re.compile(r"联邦基金利率|美联储利率决议|FOMC(声明|利率|会议$)|Federal Funds Rate|FOMC Statement", re.I)),
    ({"中国", "CNY"}, re.compile(r"官方制造业PMI|贷款市场报价利率|(?<![A-Za-z])LPR(?![A-Za-z])|^Manufacturing PMI$", re.I)),
    (None, re.compile(r"政治局|中央经济工作会议|(欧洲|日本|英国|中国)央行.*(利率决议|议息会议|政策利率)"
                      r"|Main Refinancing Rate|BOJ Policy Rate|Official Bank Rate")),
]
_NO_BOOST = re.compile(r"讲话|发表|出席|听证|票委|纪要|Speaks|Testif|Minutes", re.I)


def boost_importance(title, country, importance):
    """命中核心宏观事件 → 4; 否则原值。讲话/纪要类永不提升。"""
    text = title or ""
    if _NO_BOOST.search(text):
        return int(importance or 1)
    for countries, pat in KEY_MACRO_PATTERNS:
        if countries is not None and (country or "") not in countries:
            continue
        if pat.search(text):
            return 4
    return int(importance or 1)


def normalize_importance(source, raw):
    """各来源原始重要性 → 1-4。"""
    try:
        if source == "wscn_calendar":
            v = int(float(raw))
            return max(1, min(4, v))
        if source == "ff_calendar":
            return {"High": 4, "Medium": 2, "Low": 1}.get(str(raw), 1)
        if source == "wscn_live":
            return 3 if int(raw) >= 2 else 1
        if source == "cls_roll":
            return {"A": 4, "B": 3, "C": 2}.get(str(raw), 2)
        if source == "cls_hot":
            return 4 if int(raw) == 0 else 3
        if source == "gnews":
            n = int(raw)
            return 4 if n >= 30 else 3 if n >= 10 else 2
    except (TypeError, ValueError):
        pass
    return 1


def mega_tickers_in(title, tickers=None):
    """返回标题/代码串命中的巨头 ticker 列表 (按 MEGA_CAPS 顺序, 去重)。"""
    found = []
    codes = {t.strip().upper() for t in (tickers or "").split(",") if t.strip()}
    text = title or ""
    for tk, info in MEGA_CAPS.items():
        hit = tk in codes
        if not hit:
            for alias in info.get("aliases", []):
                if _kw_hit(text, alias):
                    hit = True
                    break
        if hit and tk not in found:
            found.append(tk)
    return found
