import { useEffect, useState, type ReactNode } from "react"
import {
  ArrowClockwise,
  Check,
  CheckCircle,
  FloppyDisk,
  Plus,
  Trash,
  WarningCircle,
} from "@phosphor-icons/react"
import { useQuery } from "@tanstack/react-query"

import { PageHeader } from "@/components/app-shell"
import { EvaluationBoundaryNote } from "@/components/evaluation-boundary-note"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { api, ApiError } from "@/lib/api"

/**
 * ADR-0033 v3 合同配置编辑器（存 → 读 → 改 → 校验）。
 *
 * 本页编辑的是隔离持久化的 v3 合同配置（红线规则 / 子类目赛道 / 每子类目共性+特有
 * 维度组 / 分类映射）。「校验」调用后端复用的确定性校验器（不落库），「保存」走
 * POST/PUT 落库并算 contract_hash、递增 revision。全程只做 CRUD + 校验，不入队、
 * 不发布、不调用模型、不接线上算分路径（当前 worker 尚未接入 v3）。
 */

const BASE = "/api/category-evaluation/v3-config"

// 与后端 redline_policy / track_classification / common_modifiers / composition
// 的 format_version 保持一致（新建空白配置时写入）。
const REDLINE_POLICY_FORMAT_VERSION = "redline-policy-v1"
const TRACK_CLASSIFICATION_FORMAT_VERSION = "track-classification-v1"
const COMMON_MODIFIERS_FORMAT_VERSION = "common-modifiers-v1"
const CONTRACT_SCHEMA_VERSION = "evaluation-category-profile-v3"
const CLASSIFICATION_MAP_FORMAT_VERSION = "subcategory-classification-map-v1"
const SUBCATEGORY_DIMENSIONS_FORMAT_VERSION = "subcategory-dimensions-v1"

type Json = Record<string, any>

type ConfigSummary = {
  id: number
  category_key: string
  display_name: string
  status: string
  revision: number
  contract_hash: string
  media_penalty_enabled: boolean
  updated_at: string
}

type ConfigDetail = ConfigSummary & {
  contract: Json
  classification_map: Json
  subcategory_dimensions: Record<string, Json>
  dimension_deduction_rules: Record<string, Json>
  created_by: string
  created_at: string
}

type ValidationErrorItem = { target: string; code: string; message: string }
type ValidateResponse = { ok: boolean; errors: ValidationErrorItem[] }

type Editable = {
  category_key: string
  display_name: string
  contract: Json
  classification_map: Json
  subcategory_dimensions: Record<string, Json>
}

const STATUS_TONE: Record<string, "success" | "active" | "neutral"> = {
  active: "success",
  draft: "active",
  retired: "neutral",
}

const STATUS_LABEL: Record<string, string> = {
  active: "已启用",
  draft: "草稿",
  retired: "已退役",
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

// 一份最小的、能通过后端校验器的空白 v3 配置：一条兜底赛道 + 一条红线 +
// 一个把兜底类目映射到该赛道的分类映射 + 该赛道的维度组（prompt-only，两组皆空）。
function blankEditable(): Editable {
  const trackKey = "class_default"
  return {
    category_key: "",
    display_name: "",
    contract: {
      schema_version: CONTRACT_SCHEMA_VERSION,
      category_key: "",
      level_semantics_version: "doc-l5-worst-v1",
      redline_policy: {
        format_version: REDLINE_POLICY_FORMAT_VERSION,
        enabled: true,
        hit_level: "L5",
        hit_score_cap: 49,
        rules: [],
      },
      track_classification: {
        format_version: TRACK_CLASSIFICATION_FORMAT_VERSION,
        default_track: trackKey,
        tracks: [
          {
            key: trackKey,
            label: "兜底赛道",
            base_score: 40,
            dimension_max: 30,
            track_cap: 70,
            dimension_schema_ref: { schema_key: "space_v13", version: "v13" },
          },
        ],
      },
      common_modifiers: {
        format_version: COMMON_MODIFIERS_FORMAT_VERSION,
        media_type_penalty: {
          enabled: true,
          baseline: "real_photo",
          penalties: { real_photo: 0, render_3d: -5, ai_image: -15, other: 0 },
        },
        high_score_veto: { threshold: 80, cap_to: 79 },
      },
    },
    classification_map: {
      format_version: CLASSIFICATION_MAP_FORMAT_VERSION,
      min_confidence: 0.6,
      category_to_subcategory: { 其它: trackKey },
      out_of_scope_subcategory: trackKey,
    },
    subcategory_dimensions: {
      [trackKey]: {
        format_version: SUBCATEGORY_DIMENSIONS_FORMAT_VERSION,
        sub_category_key: trackKey,
        dimension_max: 30,
        common_group: null,
        specific_group: null,
      },
    },
  }
}

function toEditable(detail: ConfigDetail): Editable {
  return {
    category_key: detail.category_key,
    display_name: detail.display_name,
    contract: clone(detail.contract),
    classification_map: clone(detail.classification_map),
    subcategory_dimensions: clone(detail.subcategory_dimensions),
  }
}

export function CategoryEvaluationV3ConfigPage() {
  const listQuery = useQuery({
    queryKey: ["category-evaluation-v3-config", "list"],
    queryFn: () => api<{ items: ConfigSummary[] }>(`${BASE}/`),
  })

  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [isNew, setIsNew] = useState(false)
  const [draft, setDraft] = useState<Editable | null>(null)
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<ValidationErrorItem[]>([])
  const [banner, setBanner] = useState<string | null>(null)

  // Load a config into the editor when a key is picked (and not editing a新建).
  useEffect(() => {
    if (isNew || selectedKey === null) return
    let cancelled = false
    setBusy(true)
    setErrors([])
    setBanner(null)
    api<ConfigDetail>(`${BASE}/${encodeURIComponent(selectedKey)}`)
      .then((detail) => {
        if (!cancelled) setDraft(toEditable(detail))
      })
      .catch((err) => {
        if (!cancelled) setBanner(errMessage(err))
      })
      .finally(() => {
        if (!cancelled) setBusy(false)
      })
    return () => {
      cancelled = true
    }
  }, [selectedKey, isNew])

  const startNew = () => {
    setIsNew(true)
    setSelectedKey(null)
    setDraft(blankEditable())
    setErrors([])
    setBanner(null)
  }

  const patchDraft = (mutator: (next: Editable) => void) => {
    setDraft((prev) => {
      if (!prev) return prev
      const next = clone(prev)
      mutator(next)
      return next
    })
  }

  // Keep contract.category_key mirrored to the top-level key for new configs.
  const syncKey = (value: string) => {
    patchDraft((next) => {
      next.category_key = value
      next.contract.category_key = value
    })
  }

  const requestBody = (source: Editable) => ({
    category_key: source.category_key,
    display_name: source.display_name,
    contract: source.contract,
    classification_map: source.classification_map,
    subcategory_dimensions: source.subcategory_dimensions,
  })

  const runValidate = async () => {
    if (!draft) return
    setBusy(true)
    setBanner(null)
    try {
      const key = draft.category_key.trim() || "candidate"
      const result = await api<ValidateResponse>(
        `${BASE}/${encodeURIComponent(key)}/validate`,
        { method: "POST", body: JSON.stringify(requestBody(draft)) },
      )
      setErrors(result.errors)
      setBanner(result.ok ? "校验通过：可以保存。" : `校验发现 ${result.errors.length} 处问题。`)
    } catch (err) {
      setBanner(errMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const save = async () => {
    if (!draft) return
    if (!draft.category_key.trim()) {
      setBanner("请先填写 category_key。")
      return
    }
    setBusy(true)
    setBanner(null)
    setErrors([])
    try {
      const detail = isNew
        ? await api<ConfigDetail>(`${BASE}/`, {
            method: "POST",
            body: JSON.stringify(requestBody(draft)),
          })
        : await api<ConfigDetail>(`${BASE}/${encodeURIComponent(draft.category_key)}`, {
            method: "PUT",
            body: JSON.stringify(requestBody(draft)),
          })
      setIsNew(false)
      setSelectedKey(detail.category_key)
      setDraft(toEditable(detail))
      setBanner(`已保存：${detail.category_key}（revision ${detail.revision}，hash ${detail.contract_hash.slice(0, 12)}…）`)
      await listQuery.refetch()
    } catch (err) {
      if (err instanceof ApiError && Array.isArray(err.detail?.errors)) {
        setErrors(err.detail?.errors as ValidationErrorItem[])
      }
      setBanner(errMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const changeStatus = async (status: string) => {
    if (!draft || isNew) return
    setBusy(true)
    setBanner(null)
    try {
      const detail = await api<ConfigDetail>(
        `${BASE}/${encodeURIComponent(draft.category_key)}/status`,
        { method: "PUT", body: JSON.stringify({ status }) },
      )
      setDraft(toEditable(detail))
      setBanner(`状态已改为 ${STATUS_LABEL[detail.status] ?? detail.status}。`)
      await listQuery.refetch()
    } catch (err) {
      setBanner(errMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const items = listQuery.data?.items ?? []

  return (
    <>
      <PageHeader
        index="A.7"
        title="类目评测 v3 合同配置"
        description="编辑并持久化类目评测 v3 合同：红线、赛道、维度扣分规则、媒介降权和分类映射。已启用配置会进入新评测流水线；保存前请先校验。"
        actions={
          <Button variant="secondary" onClick={() => listQuery.refetch()} disabled={busy}>
            <ArrowClockwise />刷新列表
          </Button>
        }
      />
      <div className="mx-auto max-w-[1540px] px-5 py-6 md:px-8 lg:px-10">
        <div className="mb-4"><EvaluationBoundaryNote slot="dimension" /></div>

        <div className="mb-4 flex items-start gap-3 border-y border-[var(--line)] bg-[#fff6e9] px-4 py-3 text-xs leading-6 text-[#7d4308]">
          <WarningCircle className="mt-0.5 shrink-0" size={16} weight="fill" />
          <p>
            本页编辑的是 <b>v3 规则扣分合同</b>。状态为「已启用」的新评测会按调用A事实预检
            → 调用B逐条判定扣分规则 → 确定性聚合执行；草稿配置不会接管旧流水线。
          </p>
        </div>

        <div className="grid gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
          {/* 左：配置列表 */}
          <aside className="border border-[var(--line)] bg-white">
            <div className="flex items-center justify-between border-b border-[var(--line)] px-4 py-3">
              <h2 className="text-sm font-bold">v3 配置</h2>
              <Button size="sm" onClick={startNew} disabled={busy}>
                <Plus />新建
              </Button>
            </div>
            {listQuery.isLoading ? (
              <p className="px-4 py-8 text-center text-xs text-[var(--muted)]">加载中…</p>
            ) : items.length === 0 ? (
              <p className="px-4 py-8 text-center text-xs text-[var(--muted)]">
                暂无配置，点「新建」创建一份 v3 合同。
              </p>
            ) : (
              <ul className="divide-y divide-[var(--line)]">
                {items.map((item) => {
                  const active = !isNew && item.category_key === selectedKey
                  return (
                    <li key={item.id}>
                      <button
                        type="button"
                        onClick={() => {
                          setIsNew(false)
                          setSelectedKey(item.category_key)
                        }}
                        className={`flex w-full flex-col gap-1 px-4 py-3 text-left text-xs ${
                          active ? "bg-[#f0f8c8]" : "hover:bg-[#f8f9f6]"
                        }`}
                      >
                        <span className="flex items-center gap-2">
                          <span className="font-data font-semibold">{item.category_key}</span>
                          <Badge tone={STATUS_TONE[item.status] ?? "neutral"}>
                            {STATUS_LABEL[item.status] ?? item.status}
                          </Badge>
                        </span>
                        <span className="text-[var(--muted)]">{item.display_name}</span>
                        <span className="text-[0.68rem] text-[var(--muted)]">
                          rev {item.revision} · {item.contract_hash.slice(0, 12)}…
                        </span>
                      </button>
                    </li>
                  )
                })}
              </ul>
            )}
          </aside>

          {/* 右：编辑器 */}
          <section className="min-w-0">
            {!draft ? (
              <div className="flex h-full min-h-[240px] items-center justify-center border border-dashed border-[var(--line-strong)] bg-white text-xs text-[var(--muted)]">
                从左侧选择一份配置，或点「新建」。
              </div>
            ) : (
              <V3ConfigEditor
                draft={draft}
                isNew={isNew}
                busy={busy}
                banner={banner}
                errors={errors}
                onDisplayName={(value) => patchDraft((next) => { next.display_name = value })}
                onKey={syncKey}
                onPatch={patchDraft}
                onValidate={runValidate}
                onSave={save}
                onStatus={changeStatus}
              />
            )}
          </section>
        </div>
      </div>
    </>
  )
}

function errMessage(err: unknown): string {
  if (err instanceof ApiError) {
    const detail: any = err.detail
    return detail?.code ? `${detail.code}: ${detail.message ?? err.message}` : err.message
  }
  return String(err)
}

const inputClass = "h-9 w-full rounded-[4px] border border-[var(--line-strong)] px-2 text-xs"
const numberClass = "h-9 w-24 rounded-[4px] border border-[var(--line-strong)] px-2 text-xs font-data"

function V3ConfigEditor({
  draft,
  isNew,
  busy,
  banner,
  errors,
  onDisplayName,
  onKey,
  onPatch,
  onValidate,
  onSave,
  onStatus,
}: {
  draft: Editable
  isNew: boolean
  busy: boolean
  banner: string | null
  errors: ValidationErrorItem[]
  onDisplayName: (value: string) => void
  onKey: (value: string) => void
  onPatch: (mutator: (next: Editable) => void) => void
  onValidate: () => void
  onSave: () => void
  onStatus: (status: string) => void
}) {
  const tracks: any[] = draft.contract?.track_classification?.tracks ?? []
  const trackKeys = tracks.map((t) => t.key).filter(Boolean)
  const bannerIsError = banner != null && !banner.startsWith("已保存") && !banner.startsWith("校验通过") && !banner.startsWith("状态已改")

  return (
    <div className="space-y-5">
      {/* 工具条 */}
      <div className="flex flex-wrap items-center justify-between gap-3 border border-[var(--line)] bg-white px-4 py-3">
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <span className="font-bold">{isNew ? "新建 v3 配置" : `编辑 ${draft.category_key}`}</span>
          {!isNew && (
            <select
              className="h-8 rounded-[4px] border border-[var(--line-strong)] px-2 text-xs"
              value=""
              onChange={(e) => { if (e.target.value) onStatus(e.target.value) }}
              disabled={busy}
              title="改变生命周期状态（不递增 revision）"
            >
              <option value="">改状态…</option>
              <option value="draft">草稿</option>
              <option value="active">启用</option>
              <option value="retired">退役</option>
            </select>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" onClick={onValidate} disabled={busy}>
            <Check />校验
          </Button>
          <Button size="sm" onClick={onSave} disabled={busy}>
            <FloppyDisk />保存
          </Button>
        </div>
      </div>

      {banner && (
        <div
          className={`flex items-start gap-2 border px-3 py-2 text-xs ${
            bannerIsError
              ? "border-[#e4b9b6] bg-[#fdf3f2] text-[#8d2924]"
              : "border-[#bdd8c7] bg-[#edf7f0] text-[#245b3b]"
          }`}
        >
          {bannerIsError ? <WarningCircle size={15} weight="fill" className="mt-0.5" /> : <CheckCircle size={15} weight="fill" className="mt-0.5" />}
          <span>{banner}</span>
        </div>
      )}

      {errors.length > 0 && (
        <div className="border border-[#e4b9b6] bg-[#fdf3f2] px-3 py-2 text-xs text-[#8d2924]">
          <p className="font-semibold">校验错误（{errors.length}）</p>
          <ul className="mt-1 space-y-1">
            {errors.map((err, i) => (
              <li key={i}>
                <span className="font-data font-semibold">{err.target}</span>
                {" · "}<span className="font-data">{err.code}</span>：{err.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 基本信息 */}
      <FieldCard title="基本信息">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="grid gap-1 text-xs">
            <span className="font-semibold">类目标识</span>
            <input
              className={inputClass}
              value={draft.category_key}
              disabled={!isNew}
              placeholder="如 inspiration_image"
              onChange={(e) => onKey(e.target.value)}
            />
            {!isNew && <span className="text-[0.68rem] text-[var(--muted)]">key 保存后不可改</span>}
          </label>
          <label className="grid gap-1 text-xs">
            <span className="font-semibold">中文名称</span>
            <input
              className={inputClass}
              value={draft.display_name}
              onChange={(e) => onDisplayName(e.target.value)}
            />
          </label>
        </div>
      </FieldCard>

      <RedlineEditor draft={draft} onPatch={onPatch} />
      <TrackEditor draft={draft} onPatch={onPatch} />
      <MediaPenaltyEditor draft={draft} onPatch={onPatch} />
      <DimensionGroupsEditor draft={draft} trackKeys={trackKeys} onPatch={onPatch} />
      <ClassificationMapEditor draft={draft} trackKeys={trackKeys} onPatch={onPatch} />
    </div>
  )
}

function FieldCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border border-[var(--line)] bg-white">
      <div className="border-b border-[var(--line)] px-4 py-3">
        <h3 className="text-sm font-bold">{title}</h3>
      </div>
      <div className="px-4 py-4">{children}</div>
    </section>
  )
}

function IconButton({ onClick, title, danger }: { onClick: () => void; title: string; danger?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={`inline-flex h-8 w-8 items-center justify-center rounded-[4px] border border-[var(--line-strong)] bg-white [&_svg]:size-4 ${
        danger ? "text-[#8d2924] hover:bg-[#fdf3f2]" : "hover:bg-[#f8f9f6]"
      }`}
    >
      {danger ? <Trash /> : <Plus />}
    </button>
  )
}

function RedlineEditor({
  draft,
  onPatch,
}: {
  draft: Editable
  onPatch: (mutator: (next: Editable) => void) => void
}) {
  const policy = draft.contract.redline_policy ?? {}
  const rules: any[] = policy.rules ?? []
  return (
    <FieldCard title="红线规则（命中直筛 L5，可增删 / 开关 / 改 match 词）">
      <div className="mb-3 flex flex-wrap items-center gap-3 text-xs">
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={policy.enabled !== false}
            onChange={(e) => onPatch((n) => { n.contract.redline_policy.enabled = e.target.checked })}
          />
          <span className="font-semibold">启用红线阶段</span>
        </label>
        <label className="flex items-center gap-2">
          <span className="font-semibold">命中等级</span>
          <select
            className="h-8 rounded-[4px] border border-[var(--line-strong)] px-2"
            value={policy.hit_level ?? "L5"}
            onChange={(e) => onPatch((n) => { n.contract.redline_policy.hit_level = e.target.value })}
          >
            {["L1", "L2", "L3", "L4", "L5"].map((lv) => <option key={lv}>{lv}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2">
          <span className="font-semibold">命中后分数上限</span>
          <input
            type="number"
            className={numberClass}
            value={policy.hit_score_cap ?? 49}
            onChange={(e) => onPatch((n) => { n.contract.redline_policy.hit_score_cap = Number(e.target.value) })}
          />
        </label>
      </div>
      <div className="space-y-2">
        {rules.map((rule, idx) => (
          <div key={idx} className="grid gap-2 border border-[var(--line)] px-3 py-2 sm:grid-cols-[110px_1fr_1fr_auto] sm:items-center">
            <input
              className={inputClass}
              placeholder="规则标识"
              value={rule.key ?? ""}
              onChange={(e) => onPatch((n) => { n.contract.redline_policy.rules[idx].key = e.target.value })}
            />
            <input
              className={inputClass}
              placeholder="规则中文名"
              value={rule.label ?? ""}
              onChange={(e) => onPatch((n) => { n.contract.redline_policy.rules[idx].label = e.target.value })}
            />
            <input
              className={inputClass}
              placeholder="match_any（逗号分隔，如 是截图）"
              value={(rule.match_any ?? []).join(",")}
              onChange={(e) => onPatch((n) => {
                n.contract.redline_policy.rules[idx].match_any = e.target.value.split(",").map((s) => s.trim()).filter(Boolean)
              })}
            />
            <div className="flex items-center gap-2">
              <label className="flex items-center gap-1 text-[0.68rem]">
                <input
                  type="checkbox"
                  checked={rule.enabled !== false}
                  onChange={(e) => onPatch((n) => { n.contract.redline_policy.rules[idx].enabled = e.target.checked })}
                />
                启用
              </label>
              <IconButton danger title="删除规则" onClick={() => onPatch((n) => { n.contract.redline_policy.rules.splice(idx, 1) })} />
            </div>
          </div>
        ))}
      </div>
      <Button
        variant="secondary"
        size="sm"
        className="mt-3"
        onClick={() => onPatch((n) => {
          n.contract.redline_policy.rules.push({
            key: "", label: "", signal: "production_fields.reason",
            match_any: [], exemptions: [], enabled: true,
          })
        })}
      >
        <Plus />新增红线规则
      </Button>
      <p className="mt-2 text-[0.68rem] text-[var(--muted)]">
        信号源固定为 production_fields.reason（调用A 事实字段）；match_any 必须是允许的 reason 枚举，否则校验会拦下。
      </p>
    </FieldCard>
  )
}

function TrackEditor({
  draft,
  onPatch,
}: {
  draft: Editable
  onPatch: (mutator: (next: Editable) => void) => void
}) {
  const tc = draft.contract.track_classification ?? {}
  const tracks: any[] = tc.tracks ?? []
  return (
    <FieldCard title="子类目赛道（分数基底 / 维度满分 / 赛道上限 / 默认兜底）">
      <div className="mb-3 flex items-center gap-2 text-xs">
        <span className="font-semibold">默认赛道</span>
        <select
          className="h-8 rounded-[4px] border border-[var(--line-strong)] px-2"
          value={tc.default_track ?? ""}
          onChange={(e) => onPatch((n) => { n.contract.track_classification.default_track = e.target.value })}
        >
          {tracks.map((t) => <option key={t.key} value={t.key}>{t.key}</option>)}
        </select>
      </div>
      <div className="space-y-2">
        {tracks.map((track, idx) => (
          <div key={idx} className="grid gap-2 border border-[var(--line)] px-3 py-2 sm:grid-cols-[1fr_1fr_auto] sm:items-start">
            <div className="grid gap-2">
              <input className={inputClass} placeholder="赛道标识" value={track.key ?? ""} onChange={(e) => onPatch((n) => { n.contract.track_classification.tracks[idx].key = e.target.value })} />
              <input className={inputClass} placeholder="赛道中文名" value={track.label ?? ""} onChange={(e) => onPatch((n) => { n.contract.track_classification.tracks[idx].label = e.target.value })} />
            </div>
            <div className="flex flex-wrap gap-2">
              <label className="grid gap-0.5 text-[0.68rem]"><span>基础分</span>
                <input type="number" className={numberClass} value={track.base_score ?? 0} onChange={(e) => onPatch((n) => { n.contract.track_classification.tracks[idx].base_score = Number(e.target.value) })} />
              </label>
              <label className="grid gap-0.5 text-[0.68rem]"><span>维度满分</span>
                <input type="number" className={numberClass} value={track.dimension_max ?? 0} onChange={(e) => onPatch((n) => { n.contract.track_classification.tracks[idx].dimension_max = Number(e.target.value) })} />
              </label>
              <label className="grid gap-0.5 text-[0.68rem]"><span>赛道分数上限</span>
                <input type="number" className={numberClass} value={track.track_cap ?? 0} onChange={(e) => onPatch((n) => { n.contract.track_classification.tracks[idx].track_cap = Number(e.target.value) })} />
              </label>
            </div>
            <IconButton danger title="删除赛道" onClick={() => onPatch((n) => { n.contract.track_classification.tracks.splice(idx, 1) })} />
          </div>
        ))}
      </div>
      <Button
        variant="secondary"
        size="sm"
        className="mt-3"
        onClick={() => onPatch((n) => {
          n.contract.track_classification.tracks.push({
            key: "", label: "", base_score: 40, dimension_max: 30, track_cap: 70,
            dimension_schema_ref: { schema_key: "space_v13", version: "v13" },
          })
        })}
      >
        <Plus />新增赛道
      </Button>
      <p className="mt-2 text-[0.68rem] text-[var(--muted)]">
        约束：base_score + dimension_max ≤ track_cap ≤ 100；default_track 必须是已定义的 key。
      </p>
    </FieldCard>
  )
}

const MEDIA_LABELS: Record<string, string> = {
  real_photo: "实拍照片",
  render_3d: "3D 效果图",
  ai_image: "AI 图片",
  other: "其他媒介",
}

function MediaPenaltyEditor({
  draft,
  onPatch,
}: {
  draft: Editable
  onPatch: (mutator: (next: Editable) => void) => void
}) {
  const config = draft.contract?.common_modifiers?.media_type_penalty ?? {}
  const enabled = config.enabled !== false
  const penalties = config.penalties ?? {}
  return (
    <FieldCard title="媒介降权（可独立关闭）">
      <label className="mb-3 flex items-center gap-2 text-xs font-semibold">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(event) => onPatch((next) => {
            next.contract.common_modifiers.media_type_penalty.enabled = event.target.checked
          })}
        />
        启用媒介降权
      </label>
      <div className={`grid gap-2 sm:grid-cols-2 lg:grid-cols-4 ${enabled ? "" : "opacity-45"}`}>
        {Object.entries(MEDIA_LABELS).map(([key, label]) => (
          <label key={key} className="grid gap-1 text-xs">
            <span className="font-semibold">{label}扣分值</span>
            <input
              type="number"
              className={numberClass}
              disabled={!enabled}
              value={penalties[key] ?? 0}
              onChange={(event) => onPatch((next) => {
                next.contract.common_modifiers.media_type_penalty.penalties[key] = Number(event.target.value)
              })}
            />
          </label>
        ))}
      </div>
      {!enabled && <p className="mt-2 text-[0.68rem] text-[var(--muted)]">关闭后聚合器跳过此节点，媒介扣分固定为 0。</p>}
    </FieldCard>
  )
}

function emptyGroup(): Json {
  return {
    group_weight: 0.5,
    schema_definition: {
      format_version: "dimension-schema-definition-v1",
      schema_key: "inspiration_specific",
      version: "v1",
      dimensions: [],
    },
  }
}

function DimensionGroupsEditor({
  draft,
  trackKeys,
  onPatch,
}: {
  draft: Editable
  trackKeys: string[]
  onPatch: (mutator: (next: Editable) => void) => void
}) {
  const dims = draft.subcategory_dimensions ?? {}
  const configuredKeys = Object.keys(dims)
  return (
    <FieldCard title="每子类目维度组（共性 + 特有，均可增删维度 / 调权重 / 置空）">
      <div className="space-y-4">
        {configuredKeys.length === 0 && (
          <p className="text-xs text-[var(--muted)]">尚无维度配置。为下方赛道补充配置后可编辑。</p>
        )}
        {configuredKeys.map((trackKey) => {
          const cfg = dims[trackKey] ?? {}
          return (
            <div key={trackKey} className="border border-[var(--line)] px-3 py-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="font-data text-xs font-semibold">{trackKey}</span>
                <label className="flex items-center gap-1 text-[0.68rem]">
                  维度满分
                  <input
                    type="number"
                    className={numberClass}
                    value={cfg.dimension_max ?? 0}
                    onChange={(e) => onPatch((n) => { n.subcategory_dimensions[trackKey].dimension_max = Number(e.target.value) })}
                  />
                </label>
              </div>
              {(["common_group", "specific_group"] as const).map((groupKey) => (
                <GroupEditor
                  key={groupKey}
                  trackKey={trackKey}
                  groupKey={groupKey}
                  group={cfg[groupKey]}
                  onPatch={onPatch}
                />
              ))}
            </div>
          )
        })}
      </div>
      {trackKeys.some((k) => !configuredKeys.includes(k)) && (
        <div className="mt-3 flex flex-wrap gap-2">
          {trackKeys.filter((k) => !configuredKeys.includes(k)).map((k) => (
            <Button
              key={k}
              variant="secondary"
              size="sm"
              onClick={() => onPatch((n) => {
                n.subcategory_dimensions[k] = {
                  format_version: SUBCATEGORY_DIMENSIONS_FORMAT_VERSION,
                  sub_category_key: k,
                  dimension_max: 30,
                  common_group: null,
                  specific_group: null,
                }
              })}
            >
              <Plus />为 {k} 补维度配置
            </Button>
          ))}
        </div>
      )}
    </FieldCard>
  )
}

function placeholderRules(label: string): Json[] {
  return [
    { rule_id: "minor_defect", description: `${label}存在局部轻微缺陷`, deduction: 10, tags: ["占位"] },
    { rule_id: "obvious_defect", description: `${label}存在明显缺陷`, deduction: 25, tags: ["占位"] },
    { rule_id: "severe_defect", description: `${label}存在严重缺陷`, deduction: 50, tags: ["占位"] },
  ]
}

function GroupEditor({
  trackKey,
  groupKey,
  group,
  onPatch,
}: {
  trackKey: string
  groupKey: "common_group" | "specific_group"
  group: Json | null | undefined
  onPatch: (mutator: (next: Editable) => void) => void
}) {
  const label = groupKey === "common_group" ? "共性维度组" : "特有维度组"
  const dimensions: any[] = group?.schema_definition?.dimensions ?? []
  const enabled = group != null
  return (
    <div className="mt-2 rounded-[4px] bg-[#f8fbef] px-3 py-2">
      <div className="flex items-center justify-between text-xs">
        <label className="flex items-center gap-2 font-semibold">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => onPatch((n) => {
              n.subcategory_dimensions[trackKey][groupKey] = e.target.checked ? emptyGroup() : null
            })}
          />
          {label}{!enabled && "（已置空）"}
        </label>
        {enabled && (
          <label className="flex items-center gap-1 text-[0.68rem]">
            组权重
            <input
              type="number"
              step={0.05}
              className="h-8 w-20 rounded-[4px] border border-[var(--line-strong)] px-2 font-data"
              value={group?.group_weight ?? 0}
              onChange={(e) => onPatch((n) => { n.subcategory_dimensions[trackKey][groupKey].group_weight = Number(e.target.value) })}
            />
          </label>
        )}
      </div>
      {enabled && (
        <>
          <div className="mt-2 space-y-2">
            {dimensions.map((dim, idx) => (
              <div key={idx} className="border border-[var(--line)] bg-white px-3 py-3">
                <div className="grid gap-2 sm:grid-cols-[1fr_1fr_110px_auto] sm:items-end">
                  <label className="grid gap-1 text-[0.68rem]"><span className="font-semibold">维度标识</span>
                    <input className={inputClass} placeholder="如 color_aesthetics" value={dim.key ?? ""} onChange={(e) => onPatch((n) => { n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions[idx].key = e.target.value })} />
                  </label>
                  <label className="grid gap-1 text-[0.68rem]"><span className="font-semibold">维度中文名</span>
                    <input className={inputClass} placeholder="如 色彩美感" value={dim.label ?? ""} onChange={(e) => onPatch((n) => { n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions[idx].label = e.target.value })} />
                  </label>
                  <label className="grid gap-1 text-[0.68rem]"><span className="font-semibold">维度权重</span>
                    <input type="number" step={0.05} className="h-9 w-full rounded-[4px] border border-[var(--line-strong)] px-2 text-xs font-data" value={dim.weight ?? 0} onChange={(e) => onPatch((n) => { n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions[idx].weight = Number(e.target.value) })} />
                  </label>
                  <IconButton danger title="删除维度" onClick={() => onPatch((n) => { n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions.splice(idx, 1) })} />
                </div>
                <div className="mt-3 border-t border-dashed border-[var(--line)] pt-2">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-[0.68rem] font-semibold">
                      扣分规则（调用B逐条判定） · 总扣分上限 {Math.min(100, (dim.deduction_rules ?? []).reduce((sum: number, rule: Json) => sum + Number(rule.deduction || 0), 0))}
                    </span>
                    <Button variant="ghost" size="sm" onClick={() => onPatch((n) => {
                      const rules = n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions[idx].deduction_rules ??= []
                      rules.push({ rule_id: "", description: "", deduction: 10, tags: [] })
                    })}><Plus />新增规则</Button>
                  </div>
                  <div className="space-y-1">
                    {(dim.deduction_rules ?? []).map((rule: Json, ruleIdx: number) => (
                      <div key={ruleIdx} className="grid gap-2 sm:grid-cols-[160px_1fr_110px_1fr_auto] sm:items-end">
                        <label className="grid gap-1 text-[0.68rem]"><span>规则标识</span><input className={inputClass} value={rule.rule_id ?? ""} onChange={(e) => onPatch((n) => { n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions[idx].deduction_rules[ruleIdx].rule_id = e.target.value })} /></label>
                        <label className="grid gap-1 text-[0.68rem]"><span>中文规则描述</span><input className={inputClass} value={rule.description ?? ""} onChange={(e) => onPatch((n) => { n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions[idx].deduction_rules[ruleIdx].description = e.target.value })} /></label>
                        <label className="grid gap-1 text-[0.68rem]"><span>扣分值</span><input type="number" min={0.1} max={100} className={numberClass} value={rule.deduction ?? 0} onChange={(e) => onPatch((n) => { n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions[idx].deduction_rules[ruleIdx].deduction = Number(e.target.value) })} /></label>
                        <label className="grid gap-1 text-[0.68rem]"><span>标签（逗号分隔）</span><input className={inputClass} value={(rule.tags ?? []).join(",")} onChange={(e) => onPatch((n) => { n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions[idx].deduction_rules[ruleIdx].tags = e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} /></label>
                        <IconButton danger title="删除扣分规则" onClick={() => onPatch((n) => { n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions[idx].deduction_rules.splice(ruleIdx, 1) })} />
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            ))}
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="mt-2"
            onClick={() => onPatch((n) => {
              n.subcategory_dimensions[trackKey][groupKey].schema_definition.dimensions.push({
                key: "", label: "新维度", weight: 1,
                deduction_rules: placeholderRules("新维度"),
                grade_points: { "1": 20, "2": 45, "3": 65, "4": 82, "5": 95 },
              })
            })}
          >
            <Plus />新增维度
          </Button>
        </>
      )}
    </div>
  )
}

function ClassificationMapEditor({
  draft,
  trackKeys,
  onPatch,
}: {
  draft: Editable
  trackKeys: string[]
  onPatch: (mutator: (next: Editable) => void) => void
}) {
  const map = draft.classification_map ?? {}
  const entries = Object.entries<string>(map.category_to_subcategory ?? {})
  return (
    <FieldCard title="分类映射（一级类目 → 子类目赛道）">
      <div className="mb-3 flex flex-wrap items-center gap-3 text-xs">
        <label className="flex items-center gap-2">
          <span className="font-semibold">最低分类置信度</span>
          <input
            type="number"
            step={0.05}
            min={0}
            max={1}
            className={numberClass}
            value={map.min_confidence ?? 0.6}
            onChange={(e) => onPatch((n) => { n.classification_map.min_confidence = Number(e.target.value) })}
          />
        </label>
        <label className="flex items-center gap-2">
          <span className="font-semibold">范围外兜底赛道 → </span>
          <select
            className="h-8 rounded-[4px] border border-[var(--line-strong)] px-2"
            value={map.out_of_scope_subcategory ?? ""}
            onChange={(e) => onPatch((n) => { n.classification_map.out_of_scope_subcategory = e.target.value })}
          >
            <option value="">选择赛道…</option>
            {trackKeys.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
        </label>
      </div>
      <div className="space-y-2">
        {entries.map(([category, target], idx) => (
          <div key={idx} className="grid gap-2 sm:grid-cols-[1fr_1fr_auto] sm:items-center">
            <input
              className={inputClass}
              placeholder="一级类目（如 建筑设计）"
              value={category}
              onChange={(e) => onPatch((n) => {
                const current = n.classification_map.category_to_subcategory ?? {}
                const next: Record<string, string> = {}
                Object.entries<string>(current).forEach(([c, t], i) => {
                  next[i === idx ? e.target.value : c] = t
                })
                n.classification_map.category_to_subcategory = next
              })}
            />
            <select
              className="h-9 rounded-[4px] border border-[var(--line-strong)] px-2 text-xs"
              value={target}
              onChange={(e) => onPatch((n) => { n.classification_map.category_to_subcategory[category] = e.target.value })}
            >
              <option value="">选择赛道…</option>
              {trackKeys.map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
            <IconButton danger title="删除映射" onClick={() => onPatch((n) => { delete n.classification_map.category_to_subcategory[category] })} />
          </div>
        ))}
      </div>
      <Button
        variant="secondary"
        size="sm"
        className="mt-3"
        onClick={() => onPatch((n) => {
          const map = n.classification_map.category_to_subcategory ?? {}
          if (!("" in map)) map[""] = trackKeys[0] ?? ""
          n.classification_map.category_to_subcategory = map
        })}
      >
        <Plus />新增映射
      </Button>
      <p className="mt-2 text-[0.68rem] text-[var(--muted)]">
        每个映射目标与 out_of_scope 都必须是上方已定义的赛道 key，否则校验会拦下。
      </p>
    </FieldCard>
  )
}
