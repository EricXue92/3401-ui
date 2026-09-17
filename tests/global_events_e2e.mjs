/**
 * 全球重大事件板块 — 前端验收 (API 用 route 拦截, 不依赖 DB)
 * Run: node tests/global_events_e2e.mjs   (需 3401 本地已启动: ./run_local.sh)
 */
import { chromium } from "playwright";
import { fileURLToPath } from "url";
import path from "path";
import fs from "fs";

const BASE_URL = process.env.BASE_URL || "http://127.0.0.1:3401";
const ROOT = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(ROOT, "..", "04-test", "shots");
fs.mkdirSync(OUT, { recursive: true });

const mk = (i, over = {}) => ({
  kind: "breaking",
  category: "central_bank",
  title: `事件 ${i} 美联储加息`,
  time: "2026-09-17 12:00:00",
  importance: 4,
  heat: 90 - i,
  country: "美国",
  source: "wscn_live",
  url: "https://example.com/" + i,
  tickers: null,
  summary: "摘要",
  expected: null,
  actual: null,
  previous: null,
  reading_num: 1000,
  ...over,
});
const payload = {
  time_hkt: "2026-09-17 15:20:00",
  window_start: "2026-09-17 06:00:00",
  window_end: "2026-09-18 06:00:00",
  days: 14,
  today: Array.from({ length: 10 }, (_, i) =>
    mk(
      i,
      i === 0
        ? {
            kind: "scheduled",
            category: "inflation",
            title: "8月CPI同比",
            expected: "2.9",
            actual: "3.1",
          }
        : i === 1
          ? { url: "javascript:alert(1)" }
          : {},
    ),
  ),
  upcoming: {
    "2026-09-18": [
      mk(100, {
        kind: "scheduled",
        title: "日本央行议息",
        time: "2026-09-18 12:00:00",
        importance: 4,
      }),
    ],
    "2026-09-21": [
      mk(101, {
        kind: "scheduled",
        category: "cn_policy",
        title: "9月LPR",
        time: "2026-09-21 09:00:00",
        importance: 3,
      }),
    ],
  },
  sources: {
    wscn_calendar: { ok: true },
    wscn_live: { ok: true },
    cls_roll: { ok: false, last_error: "HTTP 404" },
    gnews: { ok: true },
  },
};

let pass = 0,
  fail = 0;
const assert = (name, cond, detail = "") => {
  if (cond) {
    pass++;
    console.log("  ✅", name);
  } else {
    fail++;
    console.log("  ❌", name, detail);
  }
};

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.on("pageerror", (e) => {
  fail++;
  console.log("  ⚠️ pageerror:", e.message);
});
await page.route("**/api/event_map/global_events*", (r) =>
  r.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(payload),
  }),
);
await page.goto(BASE_URL + "/", { waitUntil: "domcontentloaded" });
await page.waitForSelector("#geToday .ge-item", { timeout: 15000 });

assert(
  "板块在实时监控之上",
  await page.evaluate(() => {
    const g = document.getElementById("globalPanel"),
      w = document.querySelector(".watch-panel");
    return (
      g &&
      w &&
      (g.compareDocumentPosition(w) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0
    );
  }),
);
const items = await page.$$eval(
  "#geToday .ge-track:first-child .ge-item",
  (els) => els.map((e) => e.textContent),
);
assert("今日 10 条", items.length === 10, String(items.length));
assert(
  "首条为排程 CPI 且显示预期/实际",
  /8月CPI同比/.test(items[0]) && /2\.9/.test(items[0]) && /3\.1/.test(items[0]),
  items[0],
);
assert("热度数字显示", /90/.test(items[0]));
assert(
  ">6 条时滚动",
  await page.$eval("#geToday", (e) => e.classList.contains("scrolling")),
);
assert(
  "hint 显示数据源不可用",
  /cls_roll/.test(await page.$eval("#geHint", (e) => e.textContent)),
);
const cells = await page.$$("#geCal .ge-cell");
assert("日历 14 格", cells.length === 14, String(cells.length));
assert(
  "09-18 有红点",
  await page
    .$eval('#geCal .ge-cell[data-date="2026-09-18"] .ge-dot.i4', () => true)
    .catch(() => false),
);
await page.click('#geCal .ge-cell[data-date="2026-09-21"]');
assert(
  "点日期切换列表",
  /9月LPR/.test(await page.$eval("#geDayList", (e) => e.textContent)),
);
await page.click("#geToday .ge-track:first-child .ge-item");
await page.waitForSelector("#modalMask.show");
assert(
  "点击打开 modal 含原文链接",
  await page.$eval("#modalBody", (e) => /查看原文/.test(e.textContent)),
);
await page.click("#modalClose");
await page.click(
  "#geToday .ge-track:first-child .ge-item:nth-child(2)",
);
await page.waitForSelector("#modalMask.show");
assert(
  "javascript: 链接不渲染为可点击 <a>",
  await page.$eval(
    "#modalBody",
    (e) =>
      !Array.from(e.querySelectorAll("a")).some((a) =>
        a.getAttribute("href").startsWith("javascript:"),
      ),
  ),
);
assert(
  "javascript: url 以纯文本展示",
  /javascript:alert\(1\)/.test(
    await page.$eval("#modalBody", (e) => e.textContent),
  ),
);
await page.screenshot({
  path: path.join(OUT, "global_events_panel.png"),
  fullPage: false,
});
await browser.close();
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
