import { lazy } from "react"

import type { MechanismEditorPlugin } from "./types.ts"

const ImageRuleEditor = lazy(async () => {
  const module = await import("./image-rule-editor")
  return { default: module.ImageRuleEditor }
})

const ProposalTextEditor = lazy(async () => {
  const module = await import("./proposal-text-editor")
  return { default: module.ProposalTextEditor }
})

const PLUGINS: Record<string, MechanismEditorPlugin> = {
  "image-rule-deduction-v1": {
    profileType: "image-rule-deduction-v1",
    canEdit: true,
    Editor: ImageRuleEditor,
    buildSummary: (revision) => revision
      ? `图像规则扣分机制 · revision ${revision.revision}`
      : "图像规则扣分机制 · 新建配置",
  },
  "text-proposal-additive-v1": {
    profileType: "text-proposal-additive-v1",
    canEdit: true,
    Editor: ProposalTextEditor,
    buildSummary: (revision) => revision
      ? `Proposal PDF 三分项加法机制 · revision ${revision.revision}`
      : "Proposal PDF 三分项加法机制",
  },
}

export function getMechanismEditorPlugin(
  profileType: string | null,
): MechanismEditorPlugin | null {
  if (!profileType) return null
  return PLUGINS[profileType] ?? null
}
