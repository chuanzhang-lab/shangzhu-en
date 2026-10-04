// 浏览器端到端冒烟（E-10）——静态扫描测不出的运行期 TypeError 在这里现形。
// 背景：2026-10-01 的翻译函数遮蔽 bug 恰恰是运行期才炸，Python 全绿照样上线。
// 本脚本（真 Chromium）：加载页面 → 点新建任务 → 断言无 console error、
// 无未捕获异常、无 /client-log 上报、无报错 toast、无 [i18n:missing:]、
// 任务真的出现在列表。
// 全程隔离：LOCAL_STORE_PATH 指向临时目录，PGDATABASE_URL 指向必败地址（自动降级 JSON）。
//
// 运行：make e2e  （或 node tests/e2e/smoke.mjs）
import { spawn } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..", "..");
const PORT = 8099;
const BASE = `http://127.0.0.1:${PORT}`;

function log(...a) { console.log("[e2e]", ...a); }
function fail(msg) { console.error("[e2e] FAIL:", msg); process.exitCode = 1; }

async function waitHealthy(timeoutMs = 15000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    try {
      const r = await fetch(`${BASE}/health`);
      if (r.ok) return;
    } catch { /* 服务还没起来 */ }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error("server did not become healthy");
}

// [i18n:missing:] 同时查 HTML 文本与属性（占位可能渲染进 title/placeholder）
async function assertNoMissingKeys(page, where) {
  const html = await page.content();
  if (html.includes("[i18n:missing:")) fail(`页面出现 [i18n:missing:]（i18n 缺键）: ${where}`);
}

const storeDir = mkdtempSync(path.join(tmpdir(), "shangzhu-e2e-"));
const server = spawn(
  path.join(ROOT, ".venv", "bin", "python3"),
  ["-c", `import uvicorn; from web_server import app; uvicorn.run(app, host="127.0.0.1", port=${PORT}, log_level="warning")`],
  {
    cwd: ROOT,
    env: {
      ...process.env,
      SHANGZHU_LOCALE: "en", // e2e 测的就是英文部署链路
      LOCAL_STORE_PATH: storeDir,
      PGDATABASE_URL: "postgresql://nobody@127.0.0.1:1/shangzhu_en_test", // 必败→降级 JSON，隔离（库名与 conftest 同源）
    },
    stdio: "ignore",
  },
);

let browser;
try {
  await waitHealthy();
  log("server healthy, launching chromium ...");

  browser = await chromium.launch();
  const page = await browser.newPage();

  const consoleErrors = [];
  const pageErrors = [];
  const failedRequests = [];
  const clientLogPosts = [];
  page.on("console", (m) => {
    if (m.type() === "error") consoleErrors.push(m.text());
  });
  page.on("pageerror", (e) => pageErrors.push(String(e)));
  page.on("requestfailed", (r) => failedRequests.push(`${r.url()} ${r.failure()?.errorText || ""}`));
  page.on("request", (r) => {
    if (r.url().endsWith("/client-log")) clientLogPosts.push(r.url());
  });

  // 1) 加载页面
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.waitForSelector("#task-list", { timeout: 5000 });
  await assertNoMissingKeys(page, "初始加载");
  log("page loaded");

  // 2) 点新建任务（2026-10-01 事故的操作路径：新建/改名/删除全炸）
  await page.click("#new-task");
  await page.waitForTimeout(1200);
  await assertNoMissingKeys(page, "新建任务后");

  // 3) 断言任务列表有新条目（新建真的成功，不是静默失败）
  const taskCount = await page.locator("#task-list .task-item").count();
  if (taskCount < 1) fail("点新建后任务列表为空——创建静默失败");

  // 4) 断言无报错 toast（事故时用户看到的「任务列表加载失败」类提示）
  //    EN 错误 toast 统一带 ⚠️ 或 "fail(ed)"；失败=zh 兜底（locale 可切换）
  const toastText = await page.locator("#toast").textContent().catch(() => "");
  if (toastText && /⚠️|失败|fail/i.test(toastText)) fail(`出现报错 toast: ${toastText}`);

  // 5) 断言零 console error / 零未捕获异常 / 零 /client-log 上报 / 零失败请求
  if (consoleErrors.length) fail(`console errors: ${JSON.stringify(consoleErrors)}`);
  if (pageErrors.length) fail(`uncaught page errors: ${JSON.stringify(pageErrors)}`);
  if (clientLogPosts.length) fail(`前端错误上报被触发（说明有静默失败）: ${clientLogPosts.length} 次`);
  if (failedRequests.length) fail(`请求失败: ${JSON.stringify(failedRequests)}`);

  // 6) E-07 版本握手：伪造「页面是旧版本」→ 与 /health 真版本不匹配 → 横幅必须出现。
  //    不改磁盘文件（改 app.js 会弄脏工作区），强制改页面自版本即可等价复现不匹配分支。
  await page.evaluate(() => { window.__APP_JS_VER__ = "stale-e2e-probe"; });
  await page.evaluate(() => window.checkVersionHandshake());
  try {
    await page.waitForSelector("#stale-banner", { timeout: 3000 });
    log("stale banner shown on version mismatch");
  } catch {
    fail("版本握手失效：伪造旧版本后过期横幅未出现");
  }
  await assertNoMissingKeys(page, "版本握手横幅");

  if (process.exitCode !== 1) log(`PASS: tasks=${taskCount}, console clean, no pageerror, no i18n missing, no client-log, handshake ok`);
} catch (e) {
  fail(String(e && e.stack ? e.stack : e));
} finally {
  if (browser) await browser.close().catch(() => {});
  server.kill("SIGTERM");
}
