import { UnknownMechanismSummary } from "./unknown-mechanism-summary"
import type { MechanismEditorProps } from "./types"

export function ProposalTextPlaceholder({ selectedRevision }: MechanismEditorProps) {
  return (
    <UnknownMechanismSummary
      detail={selectedRevision}
      reason="Proposal PDF 专用编辑器将在下一批启用；当前等级规则可安全读取和查看完整 JSON，但不会进入图像规则编辑器。"
    />
  )
}
