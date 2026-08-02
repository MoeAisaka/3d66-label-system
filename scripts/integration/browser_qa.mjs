#!/usr/bin/env node
import { createRequire } from "node:module"
import { mkdir } from "node:fs/promises"

const require = createRequire(import.meta.url)
const { chromium } = require("playwright")

const baseUrl = process.env.LABEL_LAB_BASE_URL
const password = process.env.LABEL_LAB_E2E_PASSWORD
const outputDir = process.env.LABEL_LAB_SCREENSHOT_DIR
const packageId = process.env.LABEL_LAB_PACKAGE_ID
if (!baseUrl || !password || !outputDir) {
  throw new Error("LABEL_LAB_BASE_URL, LABEL_LAB_E2E_PASSWORD and LABEL_LAB_SCREENSHOT_DIR are required")
}

const routes = [
  ["production-line", "/workflow/production-line"],
  ["materials", "/workflow/materials/packages"],
  ["jobs", "/workflow/materials/jobs"],
  ["review", "/workflow/review/low-confidence"],
  ["packages", "/workflow/releases/packages"],
  ["governance", "/workflow/governance"],
]
if (packageId) routes.push(["package-detail", `/workflow/releases/packages/${packageId}`])
const viewports = [
  ["desktop", { width: 1440, height: 1000 }],
  ["mobile", { width: 390, height: 844 }],
]

await mkdir(outputDir, { recursive: true })
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.LABEL_LAB_CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
})
const evidence = []
try {
  for (const [viewportName, viewport] of viewports) {
    const context = await browser.newContext({ viewport })
    const page = await context.newPage()
    const errors = []
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(`console:${message.text()}`)
    })
    page.on("pageerror", (error) => errors.push(`page:${error.message}`))
    await page.goto(`${baseUrl}/login`, { waitUntil: "networkidle" })
    await page.getByLabel("账号").fill("sol")
    await page.getByLabel("密码").fill(password)
    await page.getByRole("button", { name: "进入工作台" }).click()
    await page.waitForURL(/\/workflow\/production-line/)
    errors.length = 0

    for (let round = 1; round <= 5; round += 1) {
      for (const [name, route] of routes) {
        await page.goto(`${baseUrl}${route}`, { waitUntil: "networkidle" })
        const body = page.locator("body")
        const metrics = await body.evaluate(() => ({
          width: document.documentElement.clientWidth,
          scrollWidth: document.documentElement.scrollWidth,
          height: document.documentElement.clientHeight,
          scrollHeight: document.documentElement.scrollHeight,
          textLength: document.body.innerText.length,
        }))
        if (metrics.textLength < 20) throw new Error(`blank_page:${viewportName}:${round}:${name}`)
        if (metrics.scrollWidth > metrics.width + 2) {
          throw new Error(`horizontal_overflow:${viewportName}:${round}:${name}:${JSON.stringify(metrics)}`)
        }
        if (round === 1) {
          await page.screenshot({
            path: `${outputDir}/${viewportName}-${name}.png`,
            fullPage: true,
          })
        }
        evidence.push({ viewport: viewportName, round, page: name, ...metrics })
      }
    }
    if (errors.length) throw new Error(`browser_errors:${viewportName}:${errors.join("|")}`)
    await context.close()
  }
} finally {
  await browser.close()
}
console.log(JSON.stringify({ pass: true, checks: evidence.length, evidence }))
