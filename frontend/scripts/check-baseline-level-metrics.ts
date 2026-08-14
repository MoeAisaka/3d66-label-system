import assert from "node:assert/strict"
import { spawn, spawnSync } from "node:child_process"
import { existsSync, mkdtempSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"
import { fileURLToPath } from "node:url"

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const viteEntry = path.join(frontendRoot, "node_modules", "vite", "bin", "vite.js")
const port = 4176
const testUrl = `http://127.0.0.1:${port}/scripts/baseline-level-metrics-test.html`
const browserCandidates = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
]
const browser = browserCandidates.find(existsSync)

assert(browser, "未找到可用于等级指标测试的 Chrome/Chromium")

const vite = spawn(
  process.execPath,
  [viteEntry, "--host", "127.0.0.1", "--port", String(port), "--strictPort"],
  { cwd: frontendRoot, stdio: "ignore" },
)
const profileDir = mkdtempSync(path.join(tmpdir(), "labellab-level-metrics-test-"))

async function waitForVite() {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    if (vite.exitCode !== null) throw new Error(`等级指标 Vite 提前退出：${vite.exitCode}`)
    try {
      const response = await fetch(testUrl)
      if (response.ok) return
    } catch {
      // Vite is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
  throw new Error("等级指标测试页未能启动")
}

async function stopVite() {
  if (vite.exitCode !== null) return
  vite.kill("SIGTERM")
  await new Promise((resolve) => setTimeout(resolve, 250))
  if (vite.exitCode === null) vite.kill("SIGKILL")
}

try {
  await waitForVite()
  const result = spawnSync(
    browser,
    [
      "--headless=new",
      "--disable-gpu",
      "--no-sandbox",
      `--user-data-dir=${profileDir}`,
      "--window-size=1440,900",
      "--virtual-time-budget=3000",
      "--dump-dom",
      testUrl,
    ],
    { cwd: frontendRoot, encoding: "utf8", timeout: 15000 },
  )
  assert.equal(result.status, 0, "Chrome/Chromium 等级指标测试进程失败")
  assert.match(
    result.stdout,
    /data-test-status="passed"/,
    `等级指标浏览器契约未通过：${result.stdout.match(/data-test-message="([^"]*)"/)?.[1] ?? "未知错误"}`,
  )
  console.log("baseline level metrics browser contract: ok (exact, adjacent, buckets, zero denominators)")
} finally {
  await stopVite()
  rmSync(profileDir, { recursive: true, force: true })
}
