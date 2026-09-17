# 🌐 全球重大事件监控 — 设计规格

> 日期: 2026-09-17 | 状态: 待用户审阅 | 项目: 3401 事件管理系统
> 位置: 首页"🔴 实时监控"板块上方新增独立板块

---

## 1. 目标

在首页增加一个板块，回答两个问题：

1. **今天有什么会影响市场的大事？** 已排程事件（FOMC、CPI、非农、PMI、巨头财报、政策会议）与突发快讯（地缘冲突、政策突变、金融风险、能源供应链冲击）合并，按热度排序，纵向自动滚动，每 60 秒刷新。
2. **未来两周有什么大事？** 迷你日历标记未来 14 天的重要事件，点某天看当天列表。

已确认的决策：

| 决策           | 结论                                                                          |
| -------------- | ----------------------------------------------------------------------------- |
| Twitter/X 热度 | 放弃。"社媒热度"用微博/百度/头条热榜（DailyHotApi，可选）+ 财经媒体阅读数替代 |
| 热度排名       | 第一版纯规则打分，不接 LLM                                                    |
| 未来日历范围   | 14 天                                                                         |
| 中国政策会议   | 手工维护 JSON 日期表                                                          |
| 今日事件形态   | 纵向列表按热度排序，缓慢自动向上滚动，悬停暂停                                |

---

## 2. 数据源

两轮实测：开发机直连 + 用户提供的 .134 服务器连通性结果。以 .134 结果为准。

| #   | 来源                       | 接口                                                                                  | 提供                                                                                       | 开发机        | .134                                 | 用途               |
| --- | -------------------------- | ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ | ------------- | ------------------------------------ | ------------------ |
| S1  | 华尔街见闻 宏观日历        | `api-one-wscn.awtmt.com/apiv1/finance/macrodatas?start=&end=`（unix 秒，单次 ≤ 7 天） | 全球数据/央行/会议/发布会，中文标题，`importance` 1–4，预期/前值/实际                      | ✅            | 同域快讯 ✅，日历待复测              | 日历主源           |
| S1b | ForexFactory 官方 JSON     | `nfs.faireconomy.media/ff_calendar_thisweek.json`（`nextweek` 待复测）                | 本周全球宏观，英文，`impact` High/Medium/Low，带时区偏移                                   | ✅            | ✅                                   | 日历补漏与交叉校验 |
| S2  | 华尔街见闻 7x24 快讯       | `api-one.wallstcn.com/apiv1/content/lives?channel=global-channel&limit=50`            | 中文快讯，`score` 2 = 重要                                                                 | ✅            | ✅                                   | 快讯主源           |
| S3  | 财联社 电报                | `www.cls.cn/v1/roll/get_roll_list` + 签名                                             | `level` A/B/C，`reading_num`，`subjects`                                                   | ✅（带签名）  | ❌（用户实测反爬，未确认是否带签名） | 条件启用，见下     |
| S4  | 财联社 热门                | `www.cls.cn/v2/article/hot/list` + 签名                                               | 热文榜，`readNum`，`stocks`                                                                | ✅            | 同上                                 | 条件启用           |
| S5  | Google News RSS            | `news.google.com/rss/search?q=<关键词>+when:1d&hl=en-US&gl=US&ceid=US:en`             | 英文媒体 24h 报道，每查询 ≤ 100 条                                                         | ✅            | ✅                                   | 英文媒体热度       |
| S6  | DailyHotApi（自部署 Node） | `http://127.0.0.1:6688/weibo` 等                                                      | 微博/百度/头条热榜，`hot` 热度值                                                           | 公共示例站 ❌ | 需 docker 部署                       | 社媒热度加分，可选 |
| S7  | 现有 `event_earnings` 表   | 库内                                                                                  | 美股财报日期（yfinance，NVDA/AAPL/MSFT 等已全覆盖）                                        | —             | —                                    | 巨头财报           |
| S8  | 手工 JSON                  | `global_events/static/cn_policy_events.json`                                                          | 政治局会议、中央经济工作会议、两会等                                                       | —             | —                                    | 中国政策           |
| S9  | 手工 JSON                  | `global_events/static/mega_caps.json`                                                                 | 市值 > 1 万亿美元白名单：NVDA, AAPL, MSFT, GOOGL, GOOG, AMZN, META, TSLA, AVGO, TSM, BRK.B | —             | —                                    | 白名单             |

**为什么不以现有百度经济日历为主源**：从线上 API 实测 2026-09-17 共 85 条，重要性分布 1 星 61、2 星 21、3 星 1（仅英国央行），"COMEX 黄金库存"被标 2 星；09-18 仅 7 条，10-02 非农日 0 条。重要性不可用、未来覆盖薄。保留为最后兜底，不改动现有采集。

**财联社条件启用**：签名规则（来自 newsnow / RSSHub，已用 Python 验证）为固定参数 `app=CailianpressWeb&os=web&sv=7.7.5` 加业务参数，按 key 排序拼 query string，`sign = md5(sha1(qs))`，请求头带 `Referer: https://www.cls.cn/telegraph`。用户在 .134 的失败很可能是未带签名。实施 P2 时先在 .134 用带签名脚本复测；仍不通则 `config.py` 里 `GLOBAL_EVENTS_CLS_ENABLED=False` 关掉，其余不受影响。

**已知限制**

- S1 单次只能查 7 天，超出返回 `code 60502`；未来 3 周内很细，之后只剩大事件。
- S1 与 akshare `macro_info_ws` 是同一接口，不新增 akshare 调用。
- S5 无阅读数，只有报道数；GDELT 从 .134 可通但限速 5 秒 1 次，本期不用。
- 所有接口均为非官方公开接口，随时可能改版。每个来源独立 try/except，失败不影响其他来源。

不采用：金十（免费接口已下线）、Twitter/X、Google Trends（pytrends 已归档）、ForexFactory Selenium 爬虫（官方 JSON 已够）。

---

## 3. 数据模型

新表 `event_global`（追加到 `scripts/schema.sql`，建表需 DB 管理方确认后执行，沿用现有约定）：

```sql
CREATE TABLE IF NOT EXISTS `event_global` (
  `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `kind`         VARCHAR(16)  NOT NULL COMMENT 'scheduled 已排程 / breaking 突发快讯',
  `category`     VARCHAR(32)  NOT NULL COMMENT '见 §4 分类表',
  `title`        VARCHAR(255) NOT NULL,
  `summary`      TEXT                  DEFAULT NULL COMMENT '快讯正文或事件说明 (截 500 字)',
  `country`      VARCHAR(32)           DEFAULT NULL,
  `event_time`   DATETIME     NOT NULL COMMENT 'HKT; scheduled=预定时刻, breaking=发布时刻',
  `importance`   TINYINT      NOT NULL DEFAULT 1 COMMENT '归一化 1-4',
  `heat_base`    DECIMAL(8,2) NOT NULL DEFAULT 0 COMMENT '未衰减热度 0-100',
  `heat_score`   DECIMAL(8,2) NOT NULL DEFAULT 0 COMMENT '热度分 0-100, 见 §5',
  `reading_num`  INT                   DEFAULT NULL COMMENT '阅读数或报道数',
  `expected`     VARCHAR(64)           DEFAULT NULL,
  `previous`     VARCHAR(64)           DEFAULT NULL,
  `actual`       VARCHAR(64)           DEFAULT NULL,
  `tickers`      VARCHAR(255)          DEFAULT NULL COMMENT '关联标的, 逗号分隔',
  `source`       VARCHAR(32)  NOT NULL COMMENT 'wscn_calendar / ff_calendar / wscn_live / cls_roll / cls_hot / gnews',
  `source_id`    VARCHAR(64)           DEFAULT NULL COMMENT '来源侧 id',
  `url`          VARCHAR(512)          DEFAULT NULL,
  `dedup_key`    VARCHAR(64)  NOT NULL COMMENT 'sha1(source + source_id) 或 sha1(source + title + event_time)',
  `created_at`   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_global_dedup` (`dedup_key`),
  KEY `idx_global_time` (`event_time`),
  KEY `idx_global_kind_time` (`kind`, `event_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='全球重大事件 (已排程 + 突发)';
```

写入用 `INSERT ... ON DUPLICATE KEY UPDATE`，更新 `actual` / `reading_num` / `heat_score` / `importance` / `summary`。S6–S9 不落这张表：S6 热榜只在打分时读取，S7–S9 在 API 读取时合并。

---

## 4. 分类与来源映射

分类（`category`）由关键词规则从标题判定（中英文关键词各一组），取第一个命中：

| category        | 显示          | 关键词（示例，完整表在代码常量）                                                                               |
| --------------- | ------------- | -------------------------------------------------------------------------------------------------------------- |
| `central_bank`  | 🏦 央行       | 联储, FOMC, 央行, 议息, 政策利率, LPR, 加息, 降息, 缩表, 鲍威尔/沃什/植田/拉加德; Fed, rate decision, ECB, BOJ |
| `inflation`     | 📈 通胀       | CPI, PCE, PPI, 通胀; inflation                                                                                 |
| `jobs`          | 👷 就业       | 非农, 失业, ADP, 就业; payrolls, jobless                                                                       |
| `growth`        | 📊 增长       | GDP, PMI, 零售销售, 工业产出, 消费者信心; retail sales                                                         |
| `cn_policy`     | 🇨🇳 中国政策   | 政治局, 国务院, 发改委, 两会, 中央经济工作会议, 公开市场, 中国央行                                             |
| `earnings`      | 💼 财报       | 财报, 业绩, 电话会, 指引; earnings, guidance；或 tickers 命中白名单                                            |
| `geopolitics`   | ⚔️ 地缘       | 冲突, 袭击, 空袭, 停火, 制裁, 关税, 战争, 封锁, 导弹; ceasefire, strike, sanction, tariff                      |
| `energy_supply` | 🛢️ 能源供应链 | 原油, OPEC, 管道, 航运, 停产, 天然气, 港口; oil, pipeline, shipping                                            |
| `fin_risk`      | ⚠️ 金融风险   | 违约, 破产, 流动性, 挤兑, 评级下调, 暴跌, 熔断; default, bankruptcy, downgrade                                 |
| `tech_event`    | 🚀 科技大事   | 大会, 发布会, 发售, GTC, DevDay, Connect                                                                       |
| `other`         | 📌 其他       | 兜底                                                                                                           |

`importance` 归一化：

| 来源             | 规则                                        |
| ---------------- | ------------------------------------------- |
| S1 wscn 日历     | 原值 1–4                                    |
| S1b ForexFactory | High → 4，Medium → 2，Low → 1               |
| S2 wscn 快讯     | score 2 → 3；score 1 → 1                    |
| S3 cls 电报      | level A → 4，B → 3，C → 2                   |
| S4 cls 热门      | 固定 3，榜首 4                              |
| S5 Google News   | 固定 2；同一话题簇 ≥ 10 篇 → 3，≥ 30 篇 → 4 |
| S7 财报          | 白名单巨头 → 4                              |
| S8 政策          | JSON 里自带                                 |

**S1 与 S1b 合并规则**：S1b 只补漏。S1b 的 High/Medium 条目，若 S1 中存在同国家、同分类、时间相差 ≤ 30 分钟的条目，则丢弃；否则入库，标题用一张约 30 条的常见事件中文映射表（`Federal Funds Rate` → `美联储利率决议` 等），无映射保留英文。Low 不入库。

**S5 Google News 聚簇**：固定 6 个查询（央行、关税/地缘、能源、金融风险、中国政策、巨头公司名），每查询取 24h 内条目，按标题相似度（difflib ≥ 0.55）聚簇，每簇取最早一条入库，`reading_num` = 簇大小。

---

## 5. 热度打分（v1 纯规则，0–100）

**已排程事件（scheduled）**

```
base   = {4: 85, 3: 60, 2: 30, 1: 10}[importance]
+ 10   category ∈ {central_bank, inflation, jobs, cn_policy, earnings}
+ 5    country ∈ {美国, 中国, USD, CNY}
+ 15   已公布且 |actual − expected| / |expected| ≥ 5%   (意外幅度)
heat   = min(100, 合计)
```

**突发快讯（breaking）**

```
base    = {4: 70, 3: 50, 2: 25, 1: 8}[importance]
+ 0–20  reading_num 在同批次同来源内的分位 × 20
+ 15    多源同现: 24h 内另一来源有标题相似度 ≥ 0.6 的条目 (中英文分别比较, 英文簇与中文条目不比)
+ 10    category ∈ {geopolitics, fin_risk, central_bank, cn_policy, energy_supply}
+ 10    tickers 或标题命中巨头白名单
+ 15    社媒同现 (S6 启用时): 微博/百度/头条热榜任一条目与标题共享 ≥ 3 个中文二字组合
− 衰减  按发布时刻起 半衰期 3 小时: heat × 0.5^(age_h / 3)
heat    = clamp(0, 100)
```

打分在采集脚本内完成并写库 `heat_base` 与写库时刻的 `heat_score`；API 读取 breaking 时用 `heat_base` 按当前时间重新衰减，scheduled 直接用 `heat_score`。

---

## 6. 采集脚本

新增 `scripts/collect_global_events.py`，沿用现有脚本约定（`NET_TIMEOUT=10`、异常只 print 首行、时间统一 HKT、走 `db_writer.DB`）。

| 子命令       | 来源                          | cron           | 说明                                                                                                            |
| ------------ | ----------------------------- | -------------- | --------------------------------------------------------------------------------------------------------------- |
| `--calendar` | S1 + S1b                      | `*/30 * * * *` | S1 从今天 00:00 HKT 起分 2 段各 7 天拉 14 天；S1b 拉 thisweek（nextweek 可用时也拉）；合并后写 `kind=scheduled` |
| `--breaking` | S2 + S3 + S4 + S5 (+ S6 打分) | `*/3 * * * *`  | 各源拉最新条目，分类、打分、写 `kind=breaking`；同时对 24h 内已有 breaking 重算衰减                             |
| `--all`      | 全部                          | 手动           | 首次初始化                                                                                                      |
| `--check`    | 全部                          | 手动           | 只测连通性并打印各源状态，部署 .134 后先跑这个                                                                  |

`config.py` 新增开关：`GLOBAL_EVENTS_CLS_ENABLED`（默认 True）、`GLOBAL_EVENTS_HOT_URL`（DailyHotApi 地址，默认空 = 关闭）。

每次运行结束把各来源状态写到 `data/global_events_health.json`：`{source: {ok, last_success, last_error, count}}`。API 透传给前端。

cron 条目追加到 `scripts/install_cron.sh` 与 README 的 cron 表。日志 `/tmp/event_map/global_events.log`。

DailyHotApi 部署（可选，P2 末尾）：`docker run -d --name dailyhot -p 6688:6688 imsyy/dailyhot-api`，然后在 `config.py` 填地址。不部署时打分自动跳过社媒项。

---

## 7. API

`GET /api/event_map/global_events?days=14`

```json
{
  "time_hkt": "2026-09-17T15:20:00+08:00",
  "window_start": "2026-09-17T06:00:00+08:00",
  "window_end": "2026-09-18T06:00:00+08:00",
  "today": [
    {
      "kind": "breaking",
      "category": "central_bank",
      "title": "…",
      "time": "…",
      "importance": 4,
      "heat": 92.5,
      "country": "美国",
      "source": "wscn_live",
      "url": "…",
      "tickers": "",
      "summary": "…",
      "expected": null,
      "actual": null,
      "previous": null,
      "reading_num": null
    }
  ],
  "upcoming": {
    "2026-09-18": [{ "kind": "scheduled", "...": "..." }],
    "2026-09-21": []
  },
  "sources": {
    "wscn_calendar": { "ok": true, "last_success": "…", "count": 57 },
    "cls_roll": { "ok": false, "last_error": "…" }
  }
}
```

规则：

- 展示过滤（2026-09-17 用户要求）：只显示 importance = 4 且 category ∉ {tech_event, other} 的事件；核心宏观事件（美国非农/CPI/PCE/GDP/ISM、美联储/欧央行/日央行/英央行利率决议、中国官方制造业 PMI/LPR、政治局会议）在解析时固定提升为 4 星（`classify.boost_importance`）。数据照常全量采集，过滤只在 API 层。
- `today` = 当前结算窗口（沿用 `settlement_window`，HKT 06:00 → 次日 06:00）内的 scheduled（importance ≥ 2）∪ 最近 24h 的 breaking（heat ≥ 20）∪ S7 巨头财报 ∪ S8 政策事件，按 `heat` 降序，最多 40 条。
- `upcoming` = 明天起 `days` 天内的 scheduled（importance ≥ 3）∪ S7 ∪ S8，按日期分组，每天按时间排序。`days` 上限 30。
- 只读库和 JSON 文件，不请求外网；breaking 热度按读取时刻衰减。任何来源不可用都不返回 500，只在 `sources` 里标 `ok:false`。
- 库不可用时返回空数组并 `sources` 全 false。

---

## 8. 前端

在 `templates/event_map.html` 中，`.watch-panel` 之前插入 `.global-panel`。风格沿用 `.watch-panel`（圆角 14、`--shadow`、头部标题 + hint），配色用蓝紫渐变以区别于实时监控的橙色。

布局（桌面两栏，窄屏单栏）：

```
┌ 🌐 全球重大事件监控        数据源 ●●●○ · 15:20 (60s 刷新) ┐
│ 今日 · 按热度                    │ 未来 14 天                │
│ ┌──────────────────────────┐   │ 18 19 20 21 22 23 24      │
│ │ 92 🏦 02:00 美联储加息25bp │   │ ●● ●  ·  ●  ●● ●  ·       │
│ │ 88 ⚔️ 09:10 沙特管道修复  │ ↑ │ 25 26 27 28 29 30 01      │
│ │ 75 📊 20:30 初请失业金   │滚 │ ·  ●  ·  ·  ●● ●●● ●      │
│ │ …                        │动 │ ── 09-21 ──────────────   │
│ └──────────────────────────┘   │ 09:00 🇨🇳 9月 LPR   ★★★    │
└─────────────────────────────────┴───────────────────────────┘
```

- **今日列表**：每行 = 热度数字（带底色条）+ 分类徽章 + 时间 + 标题 + 重要性星 + 来源小标签；scheduled 带 `预期 / 实际`；英文条目原样显示。列表高度固定约 300 px，超出部分用 CSS `translateY` 动画缓慢上滚（内容复制一份实现无缝循环），`:hover` 暂停；条目 ≤ 6 时不滚动。点击行调用现有 `openModal` 展示详情，有 `url` 则显示"查看原文"。
- **未来日历**：14 个格子两行，每格显示日期 + 最多 3 个点（颜色按当天最高重要性：4 红、3 金、2 灰）。默认选中明天，点格子切换下方列表。周末灰底。
- **刷新**：`loadGlobalEvents()` 加入 `loadAll()`；另加 `setInterval` 60 秒仅刷新本板块（与 `loadWatch` 同样检查 `visibilityState`）。
- **降级**：接口失败显示"全球事件加载失败"；`sources` 中有 `ok:false` 时 hint 显示"部分数据源不可用: cls_roll"。
- 页脚数据源说明追加"华尔街见闻 + ForexFactory + Google News"。

---

## 9. 错误处理

| 场景                            | 处理                                                                                                                                              |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| 某来源超时 / 改版 / 返回非 JSON | 该来源跳过，health 记 `ok:false` + 首行错误；其他来源照常写库；旧数据保留                                                                         |
| S1 返回 `code 60502`            | 分段长度固定 7 天，测试覆盖                                                                                                                       |
| S1b nextweek 不可用             | 只用 thisweek，health 单独记录                                                                                                                    |
| 财联社签名失效或 .134 反爬      | health 记录；`GLOBAL_EVENTS_CLS_ENABLED=False` 关闭                                                                                               |
| DailyHotApi 未部署              | `GLOBAL_EVENTS_HOT_URL` 为空即跳过，不报错                                                                                                        |
| 标题超长 / 含 HTML              | 截 255，`summary` 截 500，前端统一 `escapeHtml`                                                                                                   |
| 重复条目                        | `dedup_key` 唯一键；scheduled 用 `source + source_id`，缺 id 时用 `source + title + event_time`                                                   |
| DB 不可用                       | 脚本 print 首行退出码 0（与现有脚本一致）；API 返回空 + `sources` 全 false                                                                        |
| 时区                            | wscn 为 unix 秒，cls 为 `ctime` unix 秒，wscn live 为 `display_time` unix 秒，ForexFactory 为 ISO 带偏移，Google News 为 RFC 822；全部转 HKT 落库 |

---

## 10. 测试

新增 `tests/test_global_events.py`（标准库 `unittest`，不新增依赖，`python3 -m unittest tests.test_global_events` 运行）：

1. **分类**：中英文标题 → 期望 category（每类 2 例 + 兜底）。
2. **重要性归一化**：各来源原始字段 → 1–4。
3. **热度打分**：scheduled 与 breaking 各 3 例，含衰减、多源同现、意外幅度、社媒同现。
4. **解析器**：用 `tests/fixtures/` 里录制的各来源样本（本次实测返回，脱敏后保存），验证解析字段、时区转换和 dedup_key；不访问网络。
5. **财联社签名**：固定参数 → 已验证通过的 sign 值。
6. **S1 分段**：14 天拆成两段各 ≤ 7 天。
7. **S1/S1b 合并**：同一 FOMC 事件只保留 S1 条目；S1 缺失时 S1b 补入并翻译标题。
8. **Google News 聚簇**：相似标题合并，簇大小写入 `reading_num`。
9. **API**：Flask test client，mock `db.query` 与 health 文件，验证 `today` 排序、`upcoming` 分组、`days` 上限、来源失败不 500。

手工验收：部署 .134 后先跑 `--check`，再 `--all`；页面出现板块，今日列表滚动，日历点亮；改坏一个来源 URL 后 hint 显示不可用且页面不报错。

---

## 11. 实施阶段

| 阶段          | 内容                                                                                                                     | 交付判定                                |
| ------------- | ------------------------------------------------------------------------------------------------------------------------ | --------------------------------------- |
| P1 已排程事件 | 建表、`--calendar`（S1 + S1b）、S7/S8/S9 JSON、API、前端板块（今日排程 + 日历）                                          | 页面能看到未来 14 天日历与今日排程事件  |
| P2 突发快讯   | `--breaking`（S2 + S5，S3/S4 复测后决定）、分类、打分、多源同现、今日列表合并滚动；末尾可选部署 DailyHotApi 接入社媒同现 | 快讯 3 分钟内出现在今日列表并按热度排序 |
| 不在本期      | LLM 归并打分、GDELT、Polymarket 赔率、推送告警                                                                           | —                                       |

---

## 12. 文件清单

| 文件                                                   | 变更                                                                      |
| ------------------------------------------------------ | ------------------------------------------------------------------------- |
| `scripts/schema.sql`                                   | 追加 `event_global` 建表                                                  |
| `scripts/collect_global_events.py`                     | 新增                                                                      |
| `scripts/db_writer.py`                                 | 新增 `save_global_events()`                                               |
| `scripts/install_cron.sh`、`README.md`                 | 追加两条 cron 与数据源表                                                  |
| `config.py`                                            | 新增 `GLOBAL_EVENTS_CLS_ENABLED`、`GLOBAL_EVENTS_HOT_URL`                 |
| `global_events/static/mega_caps.json`、`global_events/static/cn_policy_events.json`    | 新增                                                                      |
| `server.py`                                            | 新增 `/api/event_map/global_events`                                       |
| `templates/event_map.html`                             | 新增 `.global-panel` 样式、DOM、`loadGlobalEvents` / `renderGlobalEvents` |
| `tests/test_global_events.py`、`tests/fixtures/*.json` | 新增                                                                      |
| `architecture/data_sources.md`                         | 追加 S1–S6 说明与财联社签名规则                                           |
