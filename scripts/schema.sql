-- =============================================================================
-- 事件管理系统 (时间地图 :3401) — 数据层建表 SQL
-- 目标数据库: ai_market_data (MySQL, 192.168.25.92:3306)
-- 说明: 本文件仅作设计交付, 需主会话确认后才可执行。
-- 规范: utf8mb4 / InnoDB / 主键保证唯一防重复。
-- =============================================================================

SET NAMES utf8mb4;

-- -----------------------------------------------------------------------------
-- 1. event_earnings : 财报 / 经济 / 央行日历
--    同一 (ticker, event_type, event_time, title) 只保留一条, 天然防重复。
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `event_earnings` (
  `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `ticker`        VARCHAR(64)     NOT NULL                COMMENT '标的代码 (如 600519.SH / AAPL / 巴西)',
  `event_type`    VARCHAR(32)     NOT NULL                COMMENT '事件类型: earnings财报 / economic经济 / cb_rate央行决议',
  `event_time`    DATETIME        NOT NULL                COMMENT '事件时刻 (HKT)',
  `title`         VARCHAR(255)    NOT NULL                COMMENT '事件标题',
  `country`       VARCHAR(32)              DEFAULT NULL   COMMENT '地区/国家',
  `expected`      VARCHAR(64)              DEFAULT NULL   COMMENT '预期值 (原文或数字)',
  `actual`        VARCHAR(64)              DEFAULT NULL   COMMENT '实际值',
  `importance`    TINYINT                  DEFAULT NULL   COMMENT '重要性 (1-3)',
  `source`        VARCHAR(32)              DEFAULT NULL   COMMENT '数据源 (akshare/yfinance/...)',
  `created_at`    TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_earn_dedup` (`ticker`, `event_type`, `event_time`, `title`),
  KEY `idx_earn_time` (`event_time`),
  KEY `idx_earn_type` (`event_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='财报/经济/央行日历事件';

-- -----------------------------------------------------------------------------
-- 2. event_signals : 行情信号 (新高/新低/突破/市值/破发)
--    同一 (ticker, signal_type, signal_date) 只保留一条。
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `event_signals` (
  `id`               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `ticker`           VARCHAR(64)     NOT NULL                COMMENT '标的代码',
  `signal_type`      VARCHAR(32)     NOT NULL                COMMENT '信号类型: new_high新高/new_low新低/breakout突破/market_cap市值/ipo_break破发',
  `signal_date`      DATE            NOT NULL                COMMENT '信号交易日',
  `price`            DECIMAL(20,6)            DEFAULT NULL   COMMENT '触发价',
  `volume_ratio`     DECIMAL(10,4)            DEFAULT NULL   COMMENT '成交量/20日均量 (放量倍数)',
  `consecutive_days` INT                     DEFAULT NULL    COMMENT '连续创新高/新低天数',
  `params_json`      JSON                     DEFAULT NULL    COMMENT '附加参数 (阈值/前高/市值等)',
  `source`           VARCHAR(32)              DEFAULT NULL    COMMENT '数据源',
  `created_at`       TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_sig_dedup` (`ticker`, `signal_type`, `signal_date`),
  KEY `idx_sig_date` (`signal_date`),
  KEY `idx_sig_type` (`signal_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='行情触发信号';

-- -----------------------------------------------------------------------------
-- 3. event_sentiment : 舆情事件 (LLM 产出)
--    dedup_key 由 LLM 生成 (如 sha1(ticker+source+title+detected_at)), 防重复。
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `event_sentiment` (
  `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `ticker`       VARCHAR(64)     NOT NULL                COMMENT '标的代码',
  `source`       VARCHAR(32)              DEFAULT NULL    COMMENT '来源 (reddit/xueqiu/stock_news/web_search/...)',
  `title`        VARCHAR(255)             DEFAULT NULL    COMMENT '标题',
  `summary`      TEXT                     DEFAULT NULL    COMMENT '摘要',
  `sentiment`    VARCHAR(16)              DEFAULT NULL    COMMENT '情感倾向: positive/neutral/negative',
  `url`          VARCHAR(512)             DEFAULT NULL    COMMENT '原文链接',
  `detected_at`  DATETIME                 DEFAULT NULL    COMMENT '检测时间 (HKT)',
  `dedup_key`    VARCHAR(64)     NOT NULL                COMMENT 'LLM 去重键 (防重复)',
  `created_at`   TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_sent_dedup` (`dedup_key`),
  KEY `idx_sent_ticker` (`ticker`),
  KEY `idx_sent_time` (`detected_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='舆情/社区事件 (LLM产出)';

-- -----------------------------------------------------------------------------
-- 4. event_recheck_log : 复检日志
--    每次复检一行, 记录窗口判定结果与 LLM 结论。
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `event_recheck_log` (
  `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `check_time`   DATETIME        NOT NULL                COMMENT '复检执行时间 (HKT)',
  `market`       VARCHAR(32)     NOT NULL                COMMENT '市场代码 (CN/HK/US/...)',
  `window_start` DATETIME        NOT NULL                COMMENT '检查窗口开始 (HKT)',
  `window_end`   DATETIME        NOT NULL                COMMENT '检查窗口结束 (HKT)',
  `status`       VARCHAR(32)     NOT NULL                COMMENT '窗口状态: ok / empty_window空窗 / missing漏采',
  `llm_verdict`  TEXT                     DEFAULT NULL    COMMENT 'LLM 判定 (真无事件 or 漏采原因)',
  `action`       VARCHAR(64)              DEFAULT NULL    COMMENT '采取动作: none / re_collect补采 / write_back补写库',
  `note`         VARCHAR(255)             DEFAULT NULL    COMMENT '备注',
  `created_at`   TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
  PRIMARY KEY (`id`),
  KEY `idx_rchk_time` (`check_time`),
  KEY `idx_rchk_market` (`market`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='复检日志';

-- -----------------------------------------------------------------------------
-- 5. event_global : 全球重大事件 (已排程 + 突发快讯)  2026-09-17 新增
--    dedup_key 唯一, INSERT ... ON DUPLICATE KEY UPDATE 幂等。
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `event_global` (
  `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `kind`         VARCHAR(16)  NOT NULL COMMENT 'scheduled 已排程 / breaking 突发快讯',
  `category`     VARCHAR(32)  NOT NULL COMMENT 'central_bank/inflation/jobs/growth/cn_policy/earnings/geopolitics/energy_supply/fin_risk/tech_event/other',
  `title`        VARCHAR(255) NOT NULL COMMENT '标题',
  `summary`      TEXT                  DEFAULT NULL COMMENT '快讯正文或事件说明 (截 500 字)',
  `country`      VARCHAR(32)           DEFAULT NULL COMMENT '国家/地区',
  `event_time`   DATETIME     NOT NULL COMMENT 'HKT; scheduled=预定时刻, breaking=发布时刻',
  `importance`   TINYINT      NOT NULL DEFAULT 1 COMMENT '归一化 1-4',
  `heat_base`    DECIMAL(8,2) NOT NULL DEFAULT 0 COMMENT '未衰减热度 0-100',
  `heat_score`   DECIMAL(8,2) NOT NULL DEFAULT 0 COMMENT '写库时刻热度 0-100',
  `reading_num`  INT                   DEFAULT NULL COMMENT '阅读数或报道数',
  `expected`     VARCHAR(64)           DEFAULT NULL COMMENT '预期',
  `previous`     VARCHAR(64)           DEFAULT NULL COMMENT '前值',
  `actual`       VARCHAR(64)           DEFAULT NULL COMMENT '实际',
  `tickers`      VARCHAR(255)          DEFAULT NULL COMMENT '关联标的, 逗号分隔',
  `source`       VARCHAR(32)  NOT NULL COMMENT 'wscn_calendar/ff_calendar/wscn_live/cls_roll/cls_hot/gnews',
  `source_id`    VARCHAR(64)           DEFAULT NULL COMMENT '来源侧 id',
  `url`          VARCHAR(512)          DEFAULT NULL COMMENT '原文链接',
  `dedup_key`    VARCHAR(64)  NOT NULL COMMENT 'sha1 去重键',
  `created_at`   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间',
  `updated_at`   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_global_dedup` (`dedup_key`),
  KEY `idx_global_time` (`event_time`),
  KEY `idx_global_kind_time` (`kind`, `event_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='全球重大事件 (已排程 + 突发)';

-- =============================================================================
-- 交付说明 (供主会话审阅):
--   1) 4 张表均以"业务键唯一索引"防重复, 配合 INSERT ... ON DUPLICATE KEY 或
--      INSERT IGNORE 使用即可保证幂等。
--   2) event_sentiment.dedup_key 由 LLM/采集方计算 (sha1), 满足"LLM产出的key"。
--   3) 时间统一 HKT (Asia/Hong_Kong)。
--   4) 本文件未实际执行, 待主会话确认。
-- =============================================================================
