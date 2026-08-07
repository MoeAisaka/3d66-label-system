import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

const listSource = readFileSync(new URL("../src/pages/review-list.tsx", import.meta.url), "utf8")
const detailSource = readFileSync(new URL("../src/pages/review-page.tsx", import.meta.url), "utf8")

assert.match(listSource, /proposal_text_pdf/)
assert.match(listSource, /PDF 文档级评分/)
assert.match(listSource, /PDF 文档级人工复核/)
assert.match(listSource, /source_pdf_document/)
assert.match(detailSource, /proposal_text_pdf/)
assert.match(detailSource, /整份源 PDF/)
assert.match(detailSource, /PDF 文档级评分证据/)
assert.match(detailSource, /PDF 文档级人工复核/)

console.log("proposal PDF review contract ok")
