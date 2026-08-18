import { Check, CheckCircle, FloppyDisk, WarningCircle } from "@phosphor-icons/react"
import { useMemo, useState, type ReactNode } from "react"

import { SecondaryDrawer } from "@/components/workspace-page"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"

import {
  patchProposalContract,
  proposalChangedPaths,
  type ProposalContractPath,
} from "./proposal-text-contract"
import type { JsonObject, MechanismEditorProps } from "./types"

const STEPS = [
  { id: "identity", label: "等级规则身份", hint: "版本、来源与显示名" },
  { id: "input", label: "PDF 输入与确定性预检", hint: "文本层、A/B 调用与审计" },
  { id: "redline", label: "红线与人工复核", hint: "淘汰封顶与 fail-closed" },
  { id: "tracks", label: "赛道与三分项评分", hint: "对象型赛道与分项上限" },
  { id: "output", label: "等级与输出字段", hint: "L1-L5 与事实字段来源" },
  { id: "regression", label: "回归与验收", hint: "指标、风险与验收说明" },
] as const

const TRACKS = ["A", "B", "C", "balanced"] as const
const LEVELS = ["L1", "L2", "L3", "L4", "L5"] as const
const inputClass = "h-10 text-xs font-data"

function asObject(value: unknown): JsonObject {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as JsonObject
    : {}
}

function listValue(value: unknown): string {
  return Array.isArray(value) ? value.join("\n") : ""
}

function parseList(value: string): string[] {
  return value.split(/\n|,/).map((item) => item.trim()).filter(Boolean)
}

function Section({ title, description, children }: {
  title: string
  description?: string
  children: ReactNode
}) {
  return (
    <section className="border-y border-[var(--line)] bg-white">
      <div className="border-b border-[var(--line)] px-5 py-4">
        <h3 className="text-base font-bold">{title}</h3>
        {description && <p className="mt-1 text-xs leading-5 text-[var(--muted)]">{description}</p>}
      </div>
      <div className="px-5 py-5">{children}</div>
    </section>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="grid min-w-0 gap-1.5 text-xs">
      <span className="font-semibold">{label}</span>
      {children}
      {hint && <span className="leading-5 text-[var(--muted)]">{hint}</span>}
    </label>
  )
}

function Toggle({ label, checked, disabled, onChange }: {
  label: string
  checked: boolean
  disabled?: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <label className="flex min-h-10 items-center justify-between gap-3 border border-[var(--line)] bg-white px-3 py-2 text-xs font-semibold">
      <span>{label}</span>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="size-4 accent-[#CCED46]"
      />
    </label>
  )
}

function ReadOnlyValue({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="border border-[var(--line)] bg-[#f6f8f3] px-3 py-2 text-xs">
      <span className="block text-[0.68rem] font-semibold text-[var(--muted)]">{label}</span>
      <span className="font-data mt-1 block break-words">{String(value ?? "—")}</span>
    </div>
  )
}

export function ProposalTextEditor({
  draft,
  runtimeRevision,
  selectedRevision,
  busy,
  banner,
  errors,
  onPatch,
  onValidate,
  onCreateCandidate,
}: MechanismEditorProps) {
  const [step, setStep] = useState(0)
  const [jsonOpen, setJsonOpen] = useState(false)
  const [diffOpen, setDiffOpen] = useState(false)
  const contract = draft.contract
  const baseContract = selectedRevision?.contract ?? runtimeRevision?.contract ?? {}
  const changedPaths = useMemo(
    () => proposalChangedPaths(baseContract, contract),
    [baseContract, contract],
  )
  const displayNameChanged = selectedRevision != null && draft.display_name !== selectedRevision.display_name
  const changeCount = changedPaths.length + (displayNameChanged ? 1 : 0)
  const bannerIsError = banner != null
    && !banner.startsWith("校验通过")
    && !banner.startsWith("候选")
    && !banner.startsWith("初始草稿")

  const patch = (path: ProposalContractPath, value: unknown) => {
    onPatch((next) => {
      next.contract = patchProposalContract(next.contract, path, value)
    })
  }
  const patchDisplayName = (value: string) => {
    onPatch((next) => {
      next.display_name = value
      next.contract = patchProposalContract(next.contract, ["display_name"], value)
    })
  }

  const content = (() => {
    switch (step) {
      case 0:
        return <IdentityStep contract={contract} displayName={draft.display_name} patch={patch} patchDisplayName={patchDisplayName} />
      case 1:
        return <PdfInputStep contract={contract} patch={patch} />
      case 2:
        return <RedlineStep contract={contract} patch={patch} />
      case 3:
        return <TrackStep contract={contract} patch={patch} />
      case 4:
        return <OutputStep contract={contract} patch={patch} />
      default:
        return <RegressionStep contract={contract} patch={patch} />
    }
  })()

  return (
    <div className="border border-[var(--line-strong)] bg-[#f6f8f3]">
      <div className="grid min-h-[720px] grid-cols-[190px_minmax(0,1fr)_280px]">
        <nav className="border-r border-[var(--line)] bg-[#11130f] p-3 text-white" aria-label="Proposal PDF 等级规则编辑步骤">
          <div className="mb-4 px-2 py-3">
            <Badge tone="active">专用机制</Badge>
            <h2 className="font-editorial mt-3 text-xl font-bold">Proposal PDF</h2>
            <p className="mt-2 text-xs leading-5 text-white/60">六步编辑完整三分项加法等级规则</p>
          </div>
          <ol className="space-y-1">
            {STEPS.map((item, index) => (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => setStep(index)}
                  className={cn(
                    "w-full border-l-2 px-3 py-3 text-left transition-colors",
                    step === index
                      ? "border-[#CCED46] bg-white/10 text-white"
                      : "border-transparent text-white/65 hover:bg-white/5 hover:text-white",
                  )}
                >
                  <span className="font-data text-[0.68rem]">0{index + 1}</span>
                  <strong className="mt-1 block text-xs">{item.label}</strong>
                  <span className="mt-1 block text-[0.68rem] leading-4 text-white/45">{item.hint}</span>
                </button>
              </li>
            ))}
          </ol>
        </nav>

        <main className="min-w-0 bg-[#f6f8f3] py-4">
          <div className="mb-4 flex items-center justify-between gap-3 px-5">
            <div>
              <span className="font-data text-[0.68rem] text-[var(--muted)]">STEP 0{step + 1}</span>
              <h2 className="font-editorial mt-1 text-2xl font-bold">{STEPS[step].label}</h2>
            </div>
            <Badge tone={selectedRevision?.status === "candidate" ? "warning" : "neutral"}>
              {selectedRevision ? `revision ${selectedRevision.revision}` : "未选版本"}
            </Badge>
          </div>
          {content}
        </main>

        <aside className="border-l border-[var(--line)] bg-white p-4">
          <div className="sticky top-4 space-y-4">
            <section>
              <p className="text-xs font-bold">候选变更</p>
              <div className="mt-2 flex items-end justify-between border-y border-[var(--line)] py-3">
                <strong className="font-editorial text-3xl">{changeCount}</strong>
                <span className="text-[0.68rem] text-[var(--muted)]">处字段变化</span>
              </div>
              <ul className="mt-3 space-y-1 text-[0.68rem] text-[var(--muted)]">
                {displayNameChanged && <li className="truncate font-data">display_name</li>}
                {changedPaths.slice(0, 6).map((path) => <li key={path} className="truncate font-data">{path}</li>)}
                {changeCount === 0 && <li>当前草稿与所选 revision 一致。</li>}
                {changeCount > 7 && <li>另有 {changeCount - 7} 处，打开差异查看。</li>}
              </ul>
            </section>

            {banner && (
              <div className={cn(
                "flex items-start gap-2 border px-3 py-2 text-xs leading-5",
                bannerIsError
                  ? "border-[#e4b9b6] bg-[#fdf3f2] text-[#8d2924]"
                  : "border-[#bdd8c7] bg-[#edf7f0] text-[#245b3b]",
              )}>
                {bannerIsError ? <WarningCircle className="mt-0.5 shrink-0" weight="fill" /> : <CheckCircle className="mt-0.5 shrink-0" weight="fill" />}
                <span>{banner}</span>
              </div>
            )}

            <section className="border-y border-[var(--line)] py-3">
              <div className="flex items-center justify-between">
                <p className="text-xs font-bold">服务端校验</p>
                <Badge tone={errors.length ? "danger" : "neutral"}>{errors.length} 项</Badge>
              </div>
              {errors.length > 0 ? (
                <ul className="mt-2 max-h-36 space-y-2 overflow-y-auto text-[0.68rem] text-[#8d2924]">
                  {errors.map((error, index) => (
                    <li key={`${error.code}-${index}`}>
                      <span className="font-data font-semibold">{error.target}</span>：{error.message}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-2 text-[0.68rem] leading-5 text-[var(--muted)]">点击校验后展示等级规则边界错误。候选不会自动发布。</p>
              )}
            </section>

            <div className="grid gap-2">
              <Button variant="secondary" size="sm" onClick={onValidate} disabled={busy}>
                <Check />校验完整等级规则
              </Button>
              <Button size="sm" onClick={onCreateCandidate} disabled={busy}>
                <FloppyDisk />创建候选版本
              </Button>
              <div className="grid grid-cols-2 gap-2">
                <Button variant="ghost" size="sm" onClick={() => setJsonOpen(true)}>完整 JSON</Button>
                <Button variant="ghost" size="sm" onClick={() => setDiffOpen(true)}>版本差异</Button>
              </div>
            </div>

            <p className="border-l-2 border-[#CCED46] pl-3 text-[0.68rem] leading-5 text-[var(--muted)]">
              机制发布轴与标签事实发布轴独立。本页只追加候选 revision，不触发激活、重跑或下游发布。
            </p>
          </div>
        </aside>
      </div>

      <SecondaryDrawer
        open={jsonOpen}
        onOpenChange={setJsonOpen}
        title="Proposal PDF 完整候选 JSON"
        description="完整对象只读预览；路径级编辑不会丢弃未知扩展字段。"
      >
        <pre className="whitespace-pre-wrap break-words bg-[#f6f8f3] p-4 font-data text-xs leading-6">{JSON.stringify({
          contract,
          classification_map: draft.classification_map,
          subcategory_dimensions: draft.subcategory_dimensions,
        }, null, 2)}</pre>
      </SecondaryDrawer>
      <SecondaryDrawer
        open={diffOpen}
        onOpenChange={setDiffOpen}
        title="所选 revision → 当前草稿"
        description="这里列出变化路径；提交时仍发送完整等级规则对象。"
      >
        <div className="space-y-2">
          {displayNameChanged && <DiffRow path="display_name" before={selectedRevision?.display_name} after={draft.display_name} />}
          {changedPaths.map((path) => (
            <DiffRow
              key={path}
              path={path}
              before={valueAtPath(baseContract, path.replace(/^contract\./, "").split("."))}
              after={valueAtPath(contract, path.replace(/^contract\./, "").split("."))}
            />
          ))}
          {changeCount === 0 && <p className="text-sm text-[var(--muted)]">暂无变更。</p>}
        </div>
      </SecondaryDrawer>
    </div>
  )
}

function valueAtPath(value: unknown, path: string[]): unknown {
  return path.reduce<unknown>((current, key) => (
    current && typeof current === "object" ? (current as JsonObject)[key] : undefined
  ), value)
}

function DiffRow({ path, before, after }: { path: string; before: unknown; after: unknown }) {
  return (
    <div className="border border-[var(--line)] p-3 text-xs">
      <p className="font-data font-semibold">{path}</p>
      <div className="mt-2 grid grid-cols-2 gap-2">
        <pre className="min-w-0 overflow-x-auto bg-[#fff0ee] p-2 font-data text-[0.68rem]">{JSON.stringify(before, null, 2)}</pre>
        <pre className="min-w-0 overflow-x-auto bg-[#edf7f0] p-2 font-data text-[0.68rem]">{JSON.stringify(after, null, 2)}</pre>
      </div>
    </div>
  )
}

function IdentityStep({ contract, displayName, patch, patchDisplayName }: {
  contract: JsonObject
  displayName: string
  patch: (path: ProposalContractPath, value: unknown) => void
  patchDisplayName: (value: string) => void
}) {
  return (
    <Section title="等级规则身份与版本" description="profile、category 与等级规则骨架固定；运营版本标识可随候选迭代。">
      <div className="grid gap-4 md:grid-cols-2">
        <ReadOnlyValue label="contract_version" value={contract.contract_version} />
        <ReadOnlyValue label="profile_type" value={contract.profile_type} />
        <ReadOnlyValue label="category_key" value={contract.category_key} />
        <Field label="显示名称"><Input className={inputClass} value={displayName} onChange={(event) => patchDisplayName(event.target.value)} /></Field>
        <Field label="等级规则 spec_version"><Input className={inputClass} maxLength={128} value={String(contract.spec_version ?? "")} onChange={(event) => patch(["spec_version"], event.target.value)} /></Field>
        <Field label="调用 A 版本"><Input className={inputClass} maxLength={128} value={String(contract.call_a_version ?? "")} onChange={(event) => patch(["call_a_version"], event.target.value)} /></Field>
        <Field label="调用 B 版本"><Input className={inputClass} maxLength={128} value={String(contract.call_b_version ?? "")} onChange={(event) => patch(["call_b_version"], event.target.value)} /></Field>
        <Field label="来源标准"><Textarea className="min-h-20 text-xs" value={String(contract.source_standard ?? "")} onChange={(event) => patch(["source_standard"], event.target.value)} /></Field>
      </div>
    </Section>
  )
}

function PdfInputStep({ contract, patch }: StepProps) {
  const channel = asObject(contract.pdf_input_channel)
  const textLayer = asObject(channel.text_layer)
  const callA = asObject(channel.call_a)
  const callB = asObject(channel.call_b)
  const audit = asObject(channel.audit)
  const prechecks = asObject(contract.deterministic_prechecks)
  return (
    <div className="space-y-4">
      <Section title="PDF 输入与确定性预检" description="完整 PDF 文本层优先，必要时 OCR；长图拼接保持关闭。">
        <div className="grid gap-3 md:grid-cols-2">
          <ReadOnlyValue label="输入 schema" value={channel.schema_version} />
          <ReadOnlyValue label="长图拼接" value={channel.long_image_stitching === false ? "禁止" : "异常开启"} />
          <Toggle label="文本层优先" checked={textLayer.primary === true} onChange={(value) => patch(["pdf_input_channel", "text_layer", "primary"], value)} />
          <Toggle label="提取全部页面" checked={textLayer.extract_all_pages === true} onChange={(value) => patch(["pdf_input_channel", "text_layer", "extract_all_pages"], value)} />
          <Toggle label="无文本层时才 OCR" checked={textLayer.ocr_only_without_text === true} onChange={(value) => patch(["pdf_input_channel", "text_layer", "ocr_only_without_text"], value)} />
        </div>
      </Section>
      <Section title="调用 A / B 参数" description="批次与代表页数量可调，但服务端限制在安全范围 1..32。">
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="调用 A 每批页数" hint="整数 1..32"><Input type="number" min={1} max={32} className={inputClass} value={Number(callA.batch_size ?? 16)} onChange={(event) => patch(["pdf_input_channel", "call_a", "batch_size"], Number(event.target.value))} /></Field>
          <Field label="调用 A 最大边长" hint="整数 512..2048"><Input type="number" min={512} max={2048} className={inputClass} value={Number(callA.max_side_px ?? 1024)} onChange={(event) => patch(["pdf_input_channel", "call_a", "max_side_px"], Number(event.target.value))} /></Field>
          <Field label="调用 A 信息合并策略"><Input className={inputClass} maxLength={128} value={String(callA.information_merge ?? "")} onChange={(event) => patch(["pdf_input_channel", "call_a", "information_merge"], event.target.value)} /></Field>
          <Field label="调用 B 代表页数量" hint="整数 1..32"><Input type="number" min={1} max={32} className={inputClass} value={Number(callB.sample_size ?? 16)} onChange={(event) => patch(["pdf_input_channel", "call_b", "sample_size"], Number(event.target.value))} /></Field>
          <Toggle label="A 扫描全部页面" checked={callA.scan_all_pages === true} disabled onChange={() => undefined} />
          <Toggle label="A 红线命中即停止" checked={callA.stop_on_redline === true} disabled onChange={() => undefined} />
          <Toggle label="B 高保真页面" checked={callB.high_fidelity === true} disabled onChange={() => undefined} />
          <Toggle label="禁止模型选页" checked={callB.model_page_selection !== true} disabled onChange={() => undefined} />
        </div>
      </Section>
      <Section title="审计与确定性规则" description="审计开关影响回归可追溯性；页数和文件可解析规则保持原始 JSON。">
        <div className="grid gap-3 md:grid-cols-3">
          {(["record_page_batches", "record_sampled_pages", "record_tokens_by_stage"] as const).map((key) => (
            <Toggle key={key} label={key} checked={audit[key] === true} onChange={(value) => patch(["pdf_input_channel", "audit", key], value)} />
          ))}
        </div>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <ReadOnlyValue label="页数预检" value={asObject(prechecks.page_count).rule} />
          <ReadOnlyValue label="文件可解析预检" value={asObject(prechecks.file_openable).rule} />
        </div>
      </Section>
    </div>
  )
}

function RedlineStep({ contract, patch }: StepProps) {
  const policy = asObject(contract.redline_policy)
  const rules = Array.isArray(policy.rules) ? policy.rules.map(asObject) : []
  const manual = asObject(contract.manual_review_policy)
  return (
    <div className="space-y-4">
      <Section title="红线与人工复核" description="红线枚举、信号源、L5 终止语义固定；命中分数上限允许人工配置。">
        <div className="grid gap-4 md:grid-cols-2">
          <ReadOnlyValue label="信号源" value={policy.signal} />
          <ReadOnlyValue label="命中等级 / 终止" value={`${String(policy.hit_level)} / ${policy.terminal === true ? "是" : "否"}`} />
          <Field label="红线命中分数上限" hint="有限数值 0..100"><Input type="number" min={0} max={100} className={inputClass} value={Number(policy.hit_score_cap ?? 20)} onChange={(event) => patch(["redline_policy", "hit_score_cap"], Number(event.target.value))} /></Field>
          <ReadOnlyValue label="人工复核行为" value={`${String(manual.behavior)} / grade_output=${String(manual.grade_output)}`} />
        </div>
      </Section>
      <Section title="六类红线规则" description="命中枚举保持冻结；可完善规则标识和运营描述。">
        <div className="space-y-3">
          {rules.map((rule, index) => (
            <div key={`${String(rule.rule_key)}-${index}`} className="grid gap-3 border border-[var(--line)] p-3 md:grid-cols-[180px_minmax(0,1fr)]">
              <div>
                <Badge tone="danger">{Array.isArray(rule.match_any) ? rule.match_any.join(" / ") : "未配置"}</Badge>
                <Input className={cn(inputClass, "mt-2")} value={String(rule.rule_key ?? "")} onChange={(event) => patch(["redline_policy", "rules", index, "rule_key"], event.target.value)} />
              </div>
              <Textarea className="min-h-20 text-xs" value={String(rule.description ?? "")} onChange={(event) => patch(["redline_policy", "rules", index, "description"], event.target.value)} />
            </div>
          ))}
        </div>
      </Section>
    </div>
  )
}

function TrackStep({ contract, patch }: StepProps) {
  const classification = asObject(contract.track_classification)
  const tracks = asObject(classification.tracks)
  const scoring = asObject(contract.scoring)
  return (
    <div className="space-y-4">
      <Section title="赛道与三分项评分" description="每条赛道的视觉、叙事、创新上限均为整数 0..100，三项总和不得超过 100。">
        <div className="space-y-4">
          {TRACKS.map((trackKey) => {
            const track = asObject(tracks[trackKey])
            return (
              <div key={trackKey} className="border border-[var(--line-strong)] bg-white p-4">
                <div className="mb-3 flex items-center gap-3"><Badge tone="active">{trackKey}</Badge><strong>{String(track.display_name ?? "")}</strong></div>
                <div className="grid gap-3 md:grid-cols-2">
                  <Field label="赛道名称"><Input className={inputClass} value={String(track.display_name ?? "")} onChange={(event) => patch(["track_classification", "tracks", trackKey, "display_name"], event.target.value)} /></Field>
                  <Field label="类目成员" hint="换行或逗号分隔；同赛道内不得重复"><Textarea className="min-h-20 text-xs" value={listValue(track.members)} onChange={(event) => patch(["track_classification", "tracks", trackKey, "members"], parseList(event.target.value))} /></Field>
                </div>
                <div className="mt-3 grid grid-cols-3 gap-3">
                  {(["visual_max", "narrative_max", "innovation_max"] as const).map((key) => (
                    <Field key={key} label={key}>
                      <Input type="number" min={0} max={100} className={inputClass} value={Number(track[key] ?? 0)} onChange={(event) => patch(["track_classification", "tracks", trackKey, key], Number(event.target.value))} />
                    </Field>
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      </Section>
      <Section title="加法评分语义" description="总分由引擎计算，模型不得直接输出 score、rate 或 grade。">
        <div className="grid gap-3 md:grid-cols-2">
          <ReadOnlyValue label="公式" value={scoring.total_formula} />
          <ReadOnlyValue label="计算方" value={scoring.computed_by} />
          <ReadOnlyValue label="三分项" value={Array.isArray(scoring.components) ? scoring.components.join(" + ") : "—"} />
          <ReadOnlyValue label="模型禁出字段" value={Array.isArray(scoring.model_must_not_output) ? scoring.model_must_not_output.join(", ") : "—"} />
        </div>
      </Section>
    </div>
  )
}

function OutputStep({ contract, patch }: StepProps) {
  const bands = asObject(contract.grade_bands)
  const output = asObject(contract.output_fields)
  const base = asObject(contract.aesthetic_base_score)
  return (
    <div className="space-y-4">
      <Section title="等级区间" description="闭区间必须从 L5 到 L1 连续且恰好覆盖 0..100。">
        <div className="overflow-x-auto border-y border-[var(--line)]">
          <div className="grid grid-cols-[100px_1fr_1fr] bg-[#f6f8f3] px-3 py-2 text-xs font-semibold"><span>等级</span><span>下界</span><span>上界</span></div>
          {LEVELS.map((level) => {
            const interval = Array.isArray(bands[level]) ? bands[level] : [0, 0]
            return (
              <div key={level} className="grid grid-cols-[100px_1fr_1fr] items-center border-t border-[var(--line)] px-3 py-2">
                <strong className="font-data text-sm">{level}</strong>
                <Input type="number" min={0} max={100} className={inputClass} value={Number(interval[0] ?? 0)} onChange={(event) => patch(["grade_bands", level, 0], Number(event.target.value))} />
                <Input type="number" min={0} max={100} className={inputClass} value={Number(interval[1] ?? 0)} onChange={(event) => patch(["grade_bands", level, 1], Number(event.target.value))} />
              </div>
            )
          })}
        </div>
      </Section>
      <Section title="输出字段来源" description="字段按调用 A、调用 B 与引擎分组；每行一个字段。">
        <div className="grid gap-4 md:grid-cols-3">
          {(["from_call_a", "from_engine", "from_call_b"] as const).map((key) => (
            <Field key={key} label={key}>
              <Textarea className="min-h-64 text-xs font-data" value={listValue(output[key])} onChange={(event) => patch(["output_fields", key], parseList(event.target.value))} />
            </Field>
          ))}
        </div>
      </Section>
      <Section title="基础美感分事实" description="该字段在红线、封顶和等级映射前固化，候选机制不得反向改写。">
        <div className="grid gap-3 md:grid-cols-3">
          <ReadOnlyValue label="字段" value={base.field} />
          <ReadOnlyValue label="不可变" value={base.immutable} />
          <ReadOnlyValue label="定义" value={base.definition} />
        </div>
      </Section>
    </div>
  )
}

function RegressionStep({ contract, patch }: StepProps) {
  const regression = asObject(contract.baseline_regression)
  return (
    <Section title="回归与验收" description="候选创建后仍需人工选择回归样本、审阅结果并决定是否进入后续发布门禁。">
      <div className="space-y-4">
        <Field label="回归流水线"><Textarea className="min-h-24 text-xs" value={String(regression.pipeline ?? "")} onChange={(event) => patch(["baseline_regression", "pipeline"], event.target.value)} /></Field>
        <Field label="验收指标" hint="每行一项"><Textarea className="min-h-52 text-xs" value={listValue(regression.metrics)} onChange={(event) => patch(["baseline_regression", "metrics"], parseList(event.target.value))} /></Field>
        <Field label="已知风险" hint="每行一项"><Textarea className="min-h-52 text-xs" value={listValue(regression.known_risks)} onChange={(event) => patch(["baseline_regression", "known_risks"], parseList(event.target.value))} /></Field>
      </div>
    </Section>
  )
}

type StepProps = {
  contract: JsonObject
  patch: (path: ProposalContractPath, value: unknown) => void
}
