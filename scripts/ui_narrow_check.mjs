#!/usr/bin/env node
/**
 * scripts/ui_narrow_check.mjs —— 候选人侧页面 375px 真机尺度实测（1001F）
 *
 * 为什么有它：`09-UI-Design.md` §10 判据 4 要求「候选人侧页面（同意／邀约）在 375px 宽度下
 * 无横向滚动」，§8 还要求这两页「单列、按钮全宽」。1001D 只落了静态判据
 * （`tests/test_candidate_pages_narrow.py`：viewport 声明／无 >375px 固定宽度／媒体查询存在），
 * 因为当时的结论是「venv 无 playwright、无浏览器二进制」。2026-10-07 复核推翻该前提：
 * 本机已装 Google Chrome，Codex 自带运行时里带 playwright ⇒ `channel: "chrome"` 可直接跑实测，
 * 不需要下载任何浏览器（⛔ 本脚本也绝不下载）。
 *
 * 本脚本 ⚠️**只测量**：把 `app/web/static/<页>.html` 喂给无头 Chrome 量尺寸、截图，
 * ⛔ 不回写任何页面文件、⛔ 不装依赖、⛔ 不访问外站（见 `context.route` 的外链拦截）。
 *
 * 用法（node 必须用运行时自带的那份绝对路径，见 opener §五）：
 *   NODE=/Users/paulshao/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
 *   "$NODE" scripts/ui_narrow_check.mjs > data/eval/ui-narrow/result.json
 *
 * stdout = 单份 JSON（供机器判据 json.loads；⛔ 任何日志都走 stderr，别污染 stdout）；
 * 退出码 0 = 两页判据全过，1 = 至少一条不过或浏览器起不来（JSON 里给出实测值/原始报错）。
 *
 * 静态喂页有两条通道，同源同实现（`serveStatic()` 唯一真源）：
 *   ① http —— 只读静态服务，`listen(0)` 随机端口，跑完关掉（opener §二.1 的正规形态）；
 *   ② route —— 进程内 `context.route()` 直接兑现请求。**只有在 listen 被沙箱拒（EPERM）时才降级走它**：
 *      Codex 会话的 macOS Seatbelt 禁 bind 本地端口，而判据闸（launchd 起、非沙箱）能 bind。
 *      两条通道喂的是同一份文件、同一条 BASE_HREF 替换，实测结果等价；JSON 里 `transport` 记明用了哪条。
 */

import { createServer } from "node:http";
import { mkdir, readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

export const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const STATIC_DIR = path.join(REPO_ROOT, "app", "web", "static");
export const SHOT_DIR = path.join(REPO_ROOT, "data", "eval", "ui-narrow");

// playwright 真源：Codex 自带运行时（本仓库 venv 没有它）。可用环境变量覆盖。
export const PLAYWRIGHT_PATH =
  process.env.HR_PLAYWRIGHT_PATH ||
  "/Users/paulshao/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright";

// 实测尺度：iPhone X/11 级窄屏（候选人用企微内置浏览器在手机上打开这两页）。
export const VIEWPORT = { width: 375, height: 812 };

// 判据（opener §二.1 ④）：无横向滚动；按钮全宽（移动端）。
export const MAX_SCROLL_WIDTH = VIEWPORT.width; // 375
export const MIN_BUTTON_WIDTH = 300;

// 两页的「主要按钮」选择器。候选人页把内容分在若干 step 里、非当前步是 `display:none`，
// 隐藏元素的 getBoundingClientRect() 恒为 0 ⇒ 测量前先把承载按钮的 step 显形
// （只改运行时 DOM，⛔ 不落盘）。这是**测量脚手架**，不是对页面的修改。
export const PAGES = [
  {
    page: "interview_consent.html", // 候选人同意页
    primary_button: "#submit-consent-btn", // 同意/拒绝的「提交」
    reveal: ["#consent-step", "#phone-step"],
    extra_buttons: [
      { selector: "#request-code-btn", label: "请求验证码" },
      { selector: "#verify-btn", label: "提交验证码" },
    ],
  },
  {
    page: "interview_invite_issue.html", // HR 侧邀约签发页（与候选人同意页同批做窄屏验收）
    primary_button: "form#issue-form button[type=submit]", // 表单「签发」
    reveal: [],
    extra_buttons: [{ selector: "#code-btn", label: "查看验证码" }],
  },
];

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".webp": "image/webp",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
};

function log(...args) {
  console.error("[ui-narrow]", ...args);
}

/**
 * 只读静态喂页（唯一真源，两条通道共用）：
 *   `/static/*`    → `app/web/static/*`（原样，不改字节）
 *   `/<页>.html`   → 该页源码，`<!--BASE_HREF-->` 换成 `<base href="/">`（后端运行时也这么替）
 *   其余一律 404（页面里的 fetch 打到本地 404，属预期——本脚本只量静态布局，不接业务接口）
 * ⛔ 不写回任何文件。
 */
export async function serveStatic(urlPath) {
  if (urlPath.startsWith("/static/")) {
    const file = path.resolve(STATIC_DIR, urlPath.slice("/static/".length));
    if (!file.startsWith(STATIC_DIR + path.sep)) {
      return { status: 403, contentType: "text/plain; charset=utf-8", body: "forbidden" };
    }
    if (!existsSync(file)) {
      return { status: 404, contentType: "text/plain; charset=utf-8", body: "not found" };
    }
    return {
      status: 200,
      contentType: MIME[path.extname(file)] || "application/octet-stream",
      body: await readFile(file),
    };
  }
  if (urlPath.endsWith(".html")) {
    const file = path.join(STATIC_DIR, path.basename(urlPath));
    if (!existsSync(file)) {
      return { status: 404, contentType: "text/plain; charset=utf-8", body: "not found" };
    }
    const html = (await readFile(file, "utf8")).replace("<!--BASE_HREF-->", '<base href="/">');
    return { status: 200, contentType: MIME[".html"], body: html };
  }
  return { status: 404, contentType: "text/plain; charset=utf-8", body: "not found" };
}

/** 通道①：只读静态服务。listen(0) 取随机端口，调用方跑完必须 close()。 */
export function startHttpTransport() {
  const server = createServer(async (req, res) => {
    try {
      const urlPath = decodeURIComponent(new URL(req.url, "http://127.0.0.1").pathname);
      const { status, contentType, body } = await serveStatic(urlPath);
      res.writeHead(status, { "Content-Type": contentType });
      res.end(body);
    } catch (err) {
      log("静态服务异常：", err && err.message);
      res.writeHead(500, { "Content-Type": "text/plain; charset=utf-8" });
      res.end("server error");
    }
  });
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () =>
      resolve({ server, origin: `http://127.0.0.1:${server.address().port}` })
    );
  });
}

async function measure(page, cfg, origin) {
  const url = `${origin}/${cfg.page}`;
  await page.goto(url, { waitUntil: "load" });

  if (cfg.reveal.length) {
    await page.evaluate((selectors) => {
      for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (el) el.style.display = "block";
      }
    }, cfg.reveal);
  }

  // ⛔ 不联网：页面若引了外链（http/https）就记下来（本次两页均无外链），请求侧另有拦截兜底。
  const external_refs = await page.evaluate(() =>
    Array.from(document.querySelectorAll("a[href],link[href],script[src],img[src],iframe[src]"))
      .map((el) => el.getAttribute("href") || el.getAttribute("src") || "")
      .filter((v) => /^https?:\/\//i.test(v))
  );

  const shot = path.join(SHOT_DIR, cfg.page.replace(/\.html$/, "") + "-375.png");
  await page.screenshot({ path: shot, fullPage: true });

  const metrics = await page.evaluate(
    ({ primarySelector, extraSelectors }) => {
      const widthOf = (sel) => {
        const el = document.querySelector(sel);
        return el ? Number(el.getBoundingClientRect().width.toFixed(2)) : null;
      };
      const btn = document.querySelector(primarySelector);
      return {
        viewport_client_width: document.documentElement.clientWidth,
        scroll_width: document.documentElement.scrollWidth,
        body_scroll_width: document.body.scrollWidth,
        button_selector: primarySelector,
        button_text: btn ? btn.textContent.trim() : null,
        button_width: widthOf(primarySelector),
        extra_buttons: extraSelectors.map(({ selector, label }) => ({
          selector,
          label,
          width: widthOf(selector),
        })),
      };
    },
    { primarySelector: cfg.primary_button, extraSelectors: cfg.extra_buttons }
  );

  return {
    page: cfg.page,
    url,
    screenshot: path.relative(REPO_ROOT, shot),
    no_horizontal_scroll:
      metrics.scroll_width <= MAX_SCROLL_WIDTH && metrics.body_scroll_width <= MAX_SCROLL_WIDTH,
    button_full_width: metrics.button_width !== null && metrics.button_width >= MIN_BUTTON_WIDTH,
    external_refs,
    ...metrics,
  };
}

export async function run() {
  const require = createRequire(import.meta.url);
  const playwright_version = require(path.join(PLAYWRIGHT_PATH, "package.json")).version;
  const { chromium } = require(PLAYWRIGHT_PATH);

  await mkdir(SHOT_DIR, { recursive: true });

  // 通道选择：先按 opener 的正规形态起 HTTP 静态服务；被沙箱拒（EPERM）才降级到进程内 route。
  let transport = "http";
  let origin = "http://127.0.0.1:9"; // route 通道的假 origin（端口 9＝discard；请求全被拦截，绝不真连）
  let httpTransport = null;
  try {
    httpTransport = await startHttpTransport();
    origin = httpTransport.origin;
  } catch (err) {
    transport = "route";
    log(
      `静态服务 listen 失败（${err && err.code ? err.code : (err && err.message) || err}）——` +
        "沙箱禁 bind 本地端口时走进程内 route 兜底，喂的还是同一份文件"
    );
  }

  let browser = null;
  let chrome_version = null;
  let failure = null;
  const pages = [];
  try {
    browser = await chromium.launch({ channel: "chrome", headless: true });
    chrome_version = browser.version();
    const context = await browser.newContext({
      viewport: VIEWPORT,
      deviceScaleFactor: 1,
      isMobile: true,
    });
    if (transport === "route") {
      // 通道②：本机假 origin 的请求全部由本进程从磁盘兑现（不碰网络）。
      await context.route("**/*", async (route) => {
        const reqUrl = new URL(route.request().url());
        if (reqUrl.hostname !== "127.0.0.1" && reqUrl.hostname !== "localhost") {
          await route.abort(); // ⛔ 不联网
          return;
        }
        const { status, contentType, body } = await serveStatic(decodeURIComponent(reqUrl.pathname));
        await route.fulfill({ status, contentType, body });
      });
    } else {
      // 通道①：本机请求原样打到上面那个只读静态服务，浏览器侧不插兜底。
      // ⛔ 不联网：只拦非本机 origin（本两页实测无外链，属兜底）。
      await context.route(
        (url) => url.hostname !== "127.0.0.1" && url.hostname !== "localhost",
        (route) => route.abort()
      );
    }
    for (const cfg of PAGES) {
      const page = await context.newPage();
      pages.push(await measure(page, cfg, origin));
      await page.close();
    }
    await context.close();
  } catch (err) {
    failure = err && err.message ? err.message : String(err);
  } finally {
    if (browser) await browser.close().catch(() => {});
    if (httpTransport) httpTransport.server.close();
  }

  const ok =
    !failure &&
    pages.length === PAGES.length &&
    pages.every((p) => p.no_horizontal_scroll && p.button_full_width);

  // error 只留原始首行（人读/机器判据都够用）；完整调用日志截断后放 error_detail，
  // 避免把 playwright 的几百行 call log 灌进 result.json。
  const failureFirstLine = failure ? failure.split("\n")[0] : null;
  const out = {
    ok,
    error: failureFirstLine,
    error_detail: failure ? failure.slice(0, 4000) : null,
    transport,
    chrome_version,
    playwright_version,
    node_version: process.version,
    viewport: VIEWPORT,
    criteria: { max_scroll_width: MAX_SCROLL_WIDTH, min_button_width: MIN_BUTTON_WIDTH },
    generated_at: new Date().toISOString(),
    pages,
  };

  if (failure) {
    log("启动 Chrome/playwright 失败：", failure);
  } else {
    log(
      "实测：" +
        pages
          .map(
            (p) =>
              `${p.page} scrollWidth=${p.scroll_width} body=${p.body_scroll_width} ` +
              `按钮=${p.button_width}（${p.no_horizontal_scroll ? "无横滚" : "有横滚"}）`
          )
          .join(" ｜ ")
    );
  }
  process.stdout.write(JSON.stringify(out, null, 2) + "\n");
  return ok ? 0 : 1;
}

const invokedDirectly =
  process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (invokedDirectly) {
  run()
    .then((code) => process.exit(code))
    .catch((err) => {
      log("未捕获异常：", err);
      process.stdout.write(
        JSON.stringify(
          {
            ok: false,
            error: String(err && err.message ? err.message.split("\n")[0] : err),
            transport: null,
            chrome_version: null,
            node_version: process.version,
            viewport: VIEWPORT,
            generated_at: new Date().toISOString(),
            pages: [],
          },
          null,
          2
        ) + "\n"
      );
      process.exit(1);
    });
}
