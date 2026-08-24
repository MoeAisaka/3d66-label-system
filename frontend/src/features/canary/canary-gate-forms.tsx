import { useState, type FormEvent } from "react"

import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import type { CanaryRun } from "@/lib/types"
import { ExplicitCheckbox, Field, GateFormShell, SubmitRow, asRecord } from "@/features/canary/canary-form-primitives"

export const PREFLIGHT_SCHEMA = "p0e-xlsx-preflight-v1"

export const MANIFEST_VERSION = "p0e-frozen-manifest-v1"

export const CANDIDATE_SCHEMA = "p0e-candidate-preview-v1"

export function PreflightForm({
  pending,
  onSubmit,
}: {
  pending: boolean
  onSubmit: (evidence: Record<string, unknown>) => void
}) {
  const [batchKey, setBatchKey] = useState("")
  const [error, setError] = useState("")

  function submit(event: FormEvent) {
    event.preventDefault()
    const normalized = batchKey.trim()
    if (!normalized.startsWith("p0e:")) {
      setError("batch_key 必须以 p0e: 开头。")
      return
    }
    setError("")
    onSubmit({ schema_version: PREFLIGHT_SCHEMA, batch_key: normalized })
  }

  return (
    <GateFormShell
      step="DRAFT → PREFLIGHT_READY"
      title="登记 XLSX 预检产物"
      warning="这是导入器产物的人工接线占位，不代表页面已经上传、读取或验证 XLSX。"
    >
      <form onSubmit={submit} className="grid gap-5 md:grid-cols-2 md:items-end">
        <Field label="schema_version（固定）">
          <Input value={PREFLIGHT_SCHEMA} readOnly className="font-data" />
        </Field>
        <Field label="batch_key">
          <Input value={batchKey} onChange={(event) => setBatchKey(event.target.value)} placeholder="p0e:…" />
        </Field>
        <SubmitRow pending={pending} error={error} label="登记预检证据" />
      </form>
    </GateFormShell>
  )
}

export function ApprovalForm({
  run,
  pending,
  onSubmit,
}: {
  run: CanaryRun
  pending: boolean
  onSubmit: (evidence: Record<string, unknown>) => void
}) {
  const preflight = asRecord(run.evidence.xlsx_preflight)
  const batchKey = typeof preflight?.batch_key === "string" ? preflight.batch_key : ""
  const [humanApproved, setHumanApproved] = useState(false)
  const [approvedBy, setApprovedBy] = useState("")
  const [mappings, setMappings] = useState("")
  const [error, setError] = useState("")

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!humanApproved) {
      setError("必须由人工显式勾选确认，不能静默默认通过。")
      return
    }
    const approver = approvedBy.trim()
    if (!approver) {
      setError("请填写 approved_by。")
      return
    }
    const parsed = parseMappings(mappings)
    if (parsed.error) {
      setError(parsed.error)
      return
    }
    setError("")
    onSubmit({
      human_approved: true,
      approved_by: approver,
      batch_key: batchKey,
      applied_mappings: parsed.items,
    })
  }

  return (
    <GateFormShell
      step="PREFLIGHT_READY → APPROVALS_READY"
      title="人工确认字段映射"
      warning="映射必须逐行由人工确认；human_approved 默认不勾选，页面不会推断或静默应用 farmat → format。"
    >
      <form onSubmit={submit} className="grid gap-5">
        <div className="grid gap-5 md:grid-cols-2">
          <Field label="batch_key（继承且只读）">
            <Input value={batchKey} readOnly className="font-data" />
          </Field>
          <Field label="approved_by">
            <Input value={approvedBy} onChange={(event) => setApprovedBy(event.target.value)} placeholder="填写人工审批人标识" />
          </Field>
        </div>
        <Field label="applied_mappings（每行一个）" hint="格式：源字段 => 目标字段；没有映射时保持为空。">
          <Textarea
            value={mappings}
            onChange={(event) => setMappings(event.target.value)}
            placeholder={"farmat => format\n原字段 => 目标字段"}
          />
        </Field>
        <ExplicitCheckbox
          checked={humanApproved}
          onChange={setHumanApproved}
          label="我已人工核对上述批次和每一条字段映射，并明确批准登记。"
        />
        <SubmitRow pending={pending} error={error} label="提交人工审批证据" />
      </form>
    </GateFormShell>
  )
}

export function FreezeCapabilityForm({
  pending,
  onSubmit,
}: {
  pending: boolean
  onSubmit: (evidence: Record<string, unknown>) => void
}) {
  const [hosts, setHosts] = useState("")
  const [attested, setAttested] = useState(false)
  const [error, setError] = useState("")

  function submit(event: FormEvent) {
    event.preventDefault()
    const parsedHosts = Array.from(new Set(hosts.split(/\r?\n/).map((host) => host.trim()).filter(Boolean)))
    if (!parsedHosts.length) {
      setError("至少登记一个精确主机名。")
      return
    }
    const invalidHost = parsedHosts.find((host) => (
      host.includes("://") || /[/?#@:\s*\[\]]/.test(host)
    ))
    if (invalidHost) {
      setError(`主机名必须是无协议、路径、参数或凭据的精确主机：${invalidHost}`)
      return
    }
    if (!attested) {
      setError("必须显式确认已验证固定 IP HTTPS 能力。")
      return
    }
    setError("")
    onSubmit({ allowed_hosts: parsedHosts, pinned_https_attested: true })
  }

  return (
    <GateFormShell
      step="APPROVALS_READY → FREEZE_READY"
      title="登记受控冻结能力"
      warning="这里只登记已经由执行环境验证的能力，不会连接主机、执行 DNS 解析或下载任何图片。"
    >
      <form onSubmit={submit} className="grid gap-5">
        <Field label="allowed_hosts" hint="每行一个精确主机名，不填写协议、路径、query、fragment、userinfo 或通配符。">
          <Textarea
            value={hosts}
            onChange={(event) => setHosts(event.target.value)}
            placeholder={"images.example.internal\nstatic.example.internal"}
          />
        </Field>
        <ExplicitCheckbox
          checked={attested}
          onChange={setAttested}
          label="我确认执行环境已经验证固定 IP HTTPS 传输能力（pinned_https_attested=true）。"
        />
        <SubmitRow pending={pending} error={error} label="登记冻结能力" />
      </form>
    </GateFormShell>
  )
}

export function ManifestForm({
  pending,
  onSubmit,
}: {
  pending: boolean
  onSubmit: (evidence: Record<string, unknown>) => void
}) {
  const [expectedCount, setExpectedCount] = useState<number | "">("")
  const [frozenCount, setFrozenCount] = useState<number | "">("")
  const [complete, setComplete] = useState(false)
  const [error, setError] = useState("")

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!Number.isInteger(expectedCount) || !Number.isInteger(frozenCount)) {
      setError("请登记整数形式的来源数量与冻结数量。")
      return
    }
    if (Number(expectedCount) < 1 || Number(frozenCount) < 1) {
      setError("冻结清单至少需要一项来源资产。")
      return
    }
    if (expectedCount !== frozenCount) {
      setError("expected_source_count 必须与 frozen_source_count 完全一致。")
      return
    }
    if (!complete) {
      setError("必须根据执行器证据显式确认清单完整。")
      return
    }
    setError("")
    onSubmit({
      manifest_version: MANIFEST_VERSION,
      expected_source_count: expectedCount,
      frozen_source_count: frozenCount,
      complete: true,
      errors: [],
    })
  }

  return (
    <GateFormShell
      step="FREEZE_READY → CANDIDATE_READY"
      title="登记冻结清单证据"
      warning="当前必须根据执行器产物粘贴或登记证据；页面不会实际冻结文件，也不会替你推断清单完整。"
    >
      <form onSubmit={submit} className="grid gap-5">
        <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-4">
          <Field label="manifest_version（固定）">
            <Input value={MANIFEST_VERSION} readOnly className="font-data" />
          </Field>
          <Field label="expected_source_count">
            <Input
              type="number"
              min={1}
              step={1}
              value={expectedCount}
              onChange={(event) => setExpectedCount(event.target.value === "" ? "" : Number(event.target.value))}
            />
          </Field>
          <Field label="frozen_source_count">
            <Input
              type="number"
              min={1}
              step={1}
              value={frozenCount}
              onChange={(event) => setFrozenCount(event.target.value === "" ? "" : Number(event.target.value))}
            />
          </Field>
          <Field label="errors（固定）">
            <Input value="[]" readOnly className="font-data" />
          </Field>
        </div>
        <ExplicitCheckbox
          checked={complete}
          onChange={setComplete}
          label="我已核对执行器证据，清单完整、数量一致且 errors 为空（complete=true）。"
        />
        <SubmitRow pending={pending} error={error} label="登记冻结清单" />
      </form>
    </GateFormShell>
  )
}

export function CandidateHandoffForm({
  run,
  pending,
  onSubmit,
}: {
  run: CanaryRun
  pending: boolean
  onSubmit: (evidence: Record<string, unknown>) => void
}) {
  const [selectedCount, setSelectedCount] = useState(run.plan.target_size)
  const [complete, setComplete] = useState(false)
  const [allReview, setAllReview] = useState(false)
  const [noTruthOrGold, setNoTruthOrGold] = useState(false)
  const [error, setError] = useState("")

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!Number.isInteger(selectedCount) || selectedCount !== run.plan.target_size) {
      setError(`selected_count 必须与计划 target_size ${run.plan.target_size} 完全一致。`)
      return
    }
    if (!complete || !allReview || !noTruthOrGold) {
      setError("三个确认项都必须由人工显式勾选，不能默认通过。")
      return
    }
    setError("")
    onSubmit({
      candidate_preview: {
        schema_version: CANDIDATE_SCHEMA,
        selected_count: selectedCount,
        complete_for_requested_preview: true,
        forms_gold: false,
        downloads_performed: false,
        model_runs_performed: false,
      },
      human_review_handoff: {
        all_items_require_review: true,
        no_truth_or_gold_granted: true,
        item_count: selectedCount,
      },
    })
  }

  return (
    <GateFormShell
      step="CANDIDATE_READY → HUMAN_REVIEW_READY"
      title="登记候选预览与人工审核交接"
      warning="这一步只证明候选预览数量完整并转交逐项人工审核；不会赋予真值、Gold、下载或模型运行事实。"
    >
      <form onSubmit={submit} className="grid gap-5">
        <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-4">
          <Field label="schema_version（固定）">
            <Input value={CANDIDATE_SCHEMA} readOnly className="font-data" />
          </Field>
          <Field label="selected_count">
            <Input
              type="number"
              min={30}
              max={50}
              step={1}
              value={selectedCount}
              onChange={(event) => setSelectedCount(Number(event.target.value))}
            />
          </Field>
          <Field label="item_count（继承）">
            <Input value={selectedCount} readOnly className="font-data" />
          </Field>
          <Field label="固定安全事实">
            <div className="min-h-11 border border-[var(--line-strong)] bg-[#f1f3ef] px-3 py-2 text-xs leading-5 text-[var(--muted)]">
              forms_gold=false · downloads_performed=false · model_runs_performed=false
            </div>
          </Field>
        </div>
        <div className="grid gap-3">
          <ExplicitCheckbox
            checked={complete}
            onChange={setComplete}
            label="候选预览已满足计划数量（complete_for_requested_preview=true）。"
          />
          <ExplicitCheckbox
            checked={allReview}
            onChange={setAllReview}
            label="全部候选项都必须进入人工审核（all_items_require_review=true）。"
          />
          <ExplicitCheckbox
            checked={noTruthOrGold}
            onChange={setNoTruthOrGold}
            label="当前不授予任何人工真值或 Gold 身份（no_truth_or_gold_granted=true）。"
          />
        </div>
        <SubmitRow pending={pending} error={error} label="完成人工审核交接" />
      </form>
    </GateFormShell>
  )
}

export function parseMappings(value: string): {
  items: Array<{ source: string; target: string }>
  error?: string
} {
  const items: Array<{ source: string; target: string }> = []
  const lines = value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
  for (const [index, line] of lines.entries()) {
    const parts = line.split("=>")
    if (parts.length !== 2 || !parts[0].trim() || !parts[1].trim()) {
      return { items: [], error: `第 ${index + 1} 行格式无效，请使用“源字段 => 目标字段”。` }
    }
    items.push({ source: parts[0].trim(), target: parts[1].trim() })
  }
  return { items }
}
