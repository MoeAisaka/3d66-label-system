import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const [htmlPath, pdfPath] = process.argv.slice(2);
if (!htmlPath || !pdfPath) {
  throw new Error("usage: print_tpeng_proposal_pdf.mjs INPUT.html OUTPUT.pdf");
}

const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.TPENG_BROWSER || undefined,
});
try {
  const page = await browser.newPage({
    viewport: { width: 816, height: 1056 },
    deviceScaleFactor: 1,
  });
  await page.goto(pathToFileURL(htmlPath).href, { waitUntil: "load" });
  await page.evaluate(() => document.fonts?.ready);
  await page.pdf({
    path: pdfPath,
    format: "Letter",
    printBackground: true,
    displayHeaderFooter: false,
    preferCSSPageSize: true,
    margin: { top: "0.72in", right: "0.82in", bottom: "0.72in", left: "0.82in" },
  });
} finally {
  await browser.close();
}
