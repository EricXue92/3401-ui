# -*- coding: utf-8 -*-
"""
3401 事件管理系统 — 上游服务统一配置
====================================
所有被 3401 调用的外部服务 URL 统一集中在这里 (不再散落硬编码在各 .py)。
每项均支持环境变量覆盖 (便于不同部署环境切换), 默认值为当前生产网段。

加载方式:
  server.py (项目根)  →  `import config`
  scripts/*.py       →  `sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))` + `import config`
"""
import os

# ── 上游/依赖服务基础地址 (支持 env 覆盖) ──
# 34 系列面板 (3400/3402/3403/3404...) 在本机各跑一份实例 → 用 127.0.0.1 (同机)。
# 部署到任意机器 (.134 dev / .144 live) 同一份 config 均生效, 无需环境变量。
# 集中服务: db-pool(5050, .134) 与 4002(TV UDF, .174) 保持固定跨机地址。
SRV_3400 = os.environ.get("SRV_3400_URL", "http://127.0.0.1:3400")   # 3400 sector-stocks-fullmap-panel 放量排名 (本机)
SRV_3402 = os.environ.get("SRV_3402_URL", "http://127.0.0.1:3402")   # 3402 异常放量板块 (本机)
SRV_3403 = os.environ.get("SRV_3403_URL", "http://127.0.0.1:3403")   # 3403 swap-klines 实时 (本机)
SRV_3404 = os.environ.get("SRV_3404_URL", "http://127.0.0.1:3404")   # 3404 持续放量商品 (本机)
SRV_4002 = os.environ.get("TV4002_URL", "http://192.168.25.144:4002")     # 4002 TradingView UDF 数据源 (集中)
SRV_3401 = os.environ.get("SRV_3401_URL", "http://127.0.0.1:3401")        # 3401 自身 (诊断/脚本回环)

# ── 全球重大事件监控 (2026-09-17) ──
# 财联社接口在部分网络被反爬; 设 GLOBAL_EVENTS_CLS_ENABLED=0 可关闭 cls_roll / cls_hot 两源。
GLOBAL_EVENTS_CLS_ENABLED = os.environ.get("GLOBAL_EVENTS_CLS_ENABLED", "1") == "1"
# DailyHotApi 自部署地址 (如 http://127.0.0.1:6688); 为空 = 不用社媒热榜加分。
GLOBAL_EVENTS_HOT_URL = os.environ.get("GLOBAL_EVENTS_HOT_URL", "").rstrip("/")