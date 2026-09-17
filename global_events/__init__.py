# -*- coding: utf-8 -*-
"""全球重大事件监控 — 纯函数包 (分类 / 打分 / 来源解析 / 合并 / 健康状态)。
被 server.py 与 scripts/collect_global_events.py 共用, 不依赖 Flask 与 DB。
"""
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
