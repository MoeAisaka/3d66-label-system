import { useMemo, useState } from "react"
import { ArrowClockwise, Flask, Info, PlayCircle } from "@phosphor-icons/react"
import { useQuery } from "@tanstack/react-query"

import { PageHeader } from "@/components/app-shell"
import { EvaluationBoundaryNote } from "@/components/evaluation-boundary-note"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { api, ApiError } from "@/lib/api"

/**
 * ADR-0033 类目评测底座预览页（只读 + dry-run）。
 *
 * 展示已装配的「灵感图」等级规则（红线 / 子类目赛道 / 共性+特有维度 / 分类映射），
 * 并提供一个纯 dry-run 面板：给定一张图的调用A事实（分类/媒介/reason）与模拟的
 * 调用B维度 grade，走完整确定性链（红线→分类器→维度组合→聚合器）看最终等级与分数。
 * 全程只读、不写库、不入队、不发布，仅用于配置预览与联调。
 */

type PreviewContract = {
  contract: Record<string, any>
  classification_map: Record<string, any>
  subcategory_dimensions: Record<string, any>
  seed_version: string
}

type EvaluateResult = {
  redline: { hit: boolean; hit_rules?: string[] }
  resolved: { track_key: string; resolved_by: string; needs_review: boolean } | null
  result: {
    hard_reject: boolean
    terminated_at: string | null
    track_key: string | null
    base_score: number | null
    dimension_max: number | null
    score: number
    level: string
    raw_level: string
    caps: { cap: string; reason: string }[]
    steps: { step: string; score_after: any; note: string }[]
  }
}

const BASE = "/api/category-evaluation/preview"

const LEVEL_TONE: Record<string, "success" | "active" | "warning" | "neutral"> = {
  L1: "success",
  L2: "active",
  L3: "neutral",
  L4: "warning",
  L5: "warning",
}

export function CategoryEvaluationPreviewPage() {
  const contractQuery = useQuery({
    queryKey: ["category-evaluation-preview", "contract"],
    queryFn: () => api<PreviewContract>(`${BASE}/inspiration/contract`),
  })

  const [category, setCategory] = useState("建筑设计")
  const [trait, setTrait] = useState("实景照片")
  const [scopeStatus, setScopeStatus] = useState("in_scope")
  const [confidence, setConfidence] = useState(0.95)
  const [reason, setReason] = useState("")
  const [commonGrade, setCommonGrade] = useState(5)
  const [specificGrade, setSpecificGrade] = useState(5)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<EvaluateResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const subcategoryDims = contractQuery.data?.subcategory_dimensions ?? {}
  const tracks: any[] = contractQuery.data?.contract?.track_classification?.tracks ?? []

  const gradesByTrack = useMemo(() => {
    // Build common/specific grade maps for every track from the contract's
    // dimension configs, using the two sliders as a uniform grade per group.
    const common: Record<string, Record<string, number>> = {}
    const specific: Record<string, Record<string, number>> = {}
    for (const [trackKey, cfg] of Object.entries<any>(subcategoryDims)) {
      const commonDims = cfg?.common_group?.schema_definition?.dimensions ?? []
      const specificDims = cfg?.specific_group?.schema_definition?.dimensions ?? []
      common[trackKey] = Object.fromEntries(commonDims.map((d: any) => [d.key, commonGrade]))
      specific[trackKey] = Object.fromEntries(specificDims.map((d: any) => [d.key, specificGrade]))
    }
    return { common, specific }
  }, [subcategoryDims, commonGrade, specificGrade])

  const runEvaluate = async () => {
    setRunning(true)
    setError(null)
    setResult(null)
    const productionFields: Record<string, any> = { trait }
    if (reason.trim()) {
      productionFields.reason = reason.split(",").map((r) => r.trim()).filter(Boolean)
    }
    try {
      const data = await api<EvaluateResult>(`${BASE}/inspiration/evaluate`, {
        method: "POST",
        body: JSON.stringify({
          precheck: {
            production_fields: productionFields,
            classification: {
              scope_status: scopeStatus,
              primary_category: category,
              primary_confidence: confidence,
            },
          },
          common_grades_by_track: gradesByTrack.common,
          specific_grades_by_track: gradesByTrack.specific,
        }),
      })
      setResult(data)
    } catch (err) {
      if (err instanceof ApiError) {
        const detail: any = err.detail
        setError(detail?.code ? `${detail.code}: ${detail.message ?? ""}` : err.message)
      } else {
        setError(String(err))
      }
    } finally {
      setRunning(false)
    }
  }

  return (
    <>
      <PageHeader
        index="A.6"
        title="类目评测底座预览"
        description="只读查看「灵感图」类目的等级规则（红线 / 子类目 / 共性+特有维度 / 分类映射），并以 dry-run 方式跑通红线→分类器→维度→等级撮合器全链。此页不写库、不入队、不发布，仅用于配置预览与联调。"
        actions={
          <Button variant="secondary" onClick={() => contractQuery.refetch()}>
            <ArrowClockwise />刷新等级规则
          </Button>
        }
      />
      <div className="mx-auto max-w-[1540px] px-5 py-6 md:px-8 lg:px-10">
        <div className="mb-4"><EvaluationBoundaryNote /></div>

        <div className="flex items-start gap-3 border-y border-[var(--line)] bg-[#f6f9dc] px-4 py-3 text-xs leading-6 text-[#3d5106]">
          <Info className="mt-0.5 shrink-0" size={16} weight="fill" />
          <p>
            评测链路：红线筛查（命中直出 L5）→ 分类器（类目→子类目）→ 子类目提示词 A/B →
            维度评测（子类目共性维度 + 特有维度）→ 产出（等级 + 分数 + 固定字段）。
            当前采用文档语义 <b>L5=最差、L1=最优</b>。
          </p>
        </div>

        <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          {/* 左：等级规则只读展示 */}
          <section className="border border-[var(--line)] bg-white">
            <div className="flex items-center justify-between border-b border-[var(--line)] px-4 py-3">
              <h2 className="text-sm font-bold">灵感图等级规则（只读）</h2>
              {contractQuery.data && (
                <Badge tone="neutral">{contractQuery.data.seed_version}</Badge>
              )}
            </div>
            {contractQuery.isLoading ? (
              <p className="px-4 py-8 text-center text-xs text-[var(--muted)]">加载中…</p>
            ) : contractQuery.isError ? (
              <p className="px-4 py-8 text-center text-xs text-[#8d2924]">等级规则加载失败，请刷新。</p>
            ) : (
              <div className="space-y-4 px-4 py-4 text-xs leading-5">
                <div>
                  <p className="font-semibold">红线规则（命中→L5，可增删/开关）</p>
                  <ul className="mt-1 space-y-1">
                    {(contractQuery.data?.contract?.redline_policy?.rules ?? []).map((r: any) => (
                      <li key={r.key} className="flex items-center gap-2 text-[var(--muted)]">
                        <Badge tone={r.enabled === false ? "neutral" : "warning"}>
                          {r.enabled === false ? "已关闭" : "启用"}
                        </Badge>
                        <span>{r.label ?? r.key}</span>
                        <span className="text-[0.68rem]">match: {(r.match_any ?? []).join("/")}</span>
                      </li>
                    ))}
                  </ul>
                </div>
                <div>
                  <p className="font-semibold">子类目赛道（分数基底 / 维度满分 / 上限）</p>
                  <ul className="mt-1 space-y-1">
                    {tracks.map((t: any) => (
                      <li key={t.key} className="text-[var(--muted)]">
                        <span className="font-data font-semibold text-[var(--fg)]">{t.key}</span>
                        {" · "}{t.label} · 基底 {t.base_score} + 维度 {t.dimension_max} ≤ {t.track_cap}
                        {contractQuery.data?.contract?.track_classification?.default_track === t.key && (
                          <span className="ml-1 text-[0.68rem]">（默认兜底）</span>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
                <div>
                  <p className="font-semibold">固定通用维度（媒介降权 + 高分压分）</p>
                  <p className="mt-1 text-[var(--muted)]">
                    实拍 0 · 效果图 {contractQuery.data?.contract?.common_modifiers?.media_type_penalty?.penalties?.render_3d} ·
                    AI图 {contractQuery.data?.contract?.common_modifiers?.media_type_penalty?.penalties?.ai_image} ·
                    ≥{contractQuery.data?.contract?.common_modifiers?.high_score_veto?.threshold}分且有硬伤 →
                    封顶 {contractQuery.data?.contract?.common_modifiers?.high_score_veto?.cap_to}
                  </p>
                </div>
                <div>
                  <p className="font-semibold">每子类目维度组（共性 + 特有，均可增删/为空）</p>
                  <ul className="mt-1 space-y-1">
                    {Object.entries<any>(subcategoryDims).map(([key, cfg]) => (
                      <li key={key} className="text-[var(--muted)]">
                        <span className="font-data font-semibold text-[var(--fg)]">{key}</span>：
                        共性 {(cfg?.common_group?.schema_definition?.dimensions ?? []).length} 维（权重 {cfg?.common_group?.group_weight}）+
                        特有 {(cfg?.specific_group?.schema_definition?.dimensions ?? []).length} 维（权重 {cfg?.specific_group?.group_weight}）
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            )}
          </section>

          {/* 右：dry-run 面板 */}
          <section className="border border-[var(--line)] bg-white">
            <div className="flex items-center gap-2 border-b border-[var(--line)] px-4 py-3">
              <Flask size={18} weight="fill" />
              <h2 className="text-sm font-bold">Dry-run 试算（不写库）</h2>
            </div>
            <div className="grid gap-3 px-4 py-4 text-xs">
              <label className="grid gap-1">
                <span className="font-semibold">一级分类（调用A）</span>
                <input className="h-9 rounded-[4px] border border-[var(--line-strong)] px-2" value={category} onChange={(e) => setCategory(e.target.value)} />
              </label>
              <div className="grid grid-cols-2 gap-3">
                <label className="grid gap-1">
                  <span className="font-semibold">媒介 trait</span>
                  <select className="h-9 rounded-[4px] border border-[var(--line-strong)] px-2" value={trait} onChange={(e) => setTrait(e.target.value)}>
                    <option>实景照片</option>
                    <option>3D数字效果图</option>
                    <option>AI图</option>
                    <option>其它</option>
                  </select>
                </label>
                <label className="grid gap-1">
                  <span className="font-semibold">scope_status</span>
                  <select className="h-9 rounded-[4px] border border-[var(--line-strong)] px-2" value={scopeStatus} onChange={(e) => setScopeStatus(e.target.value)}>
                    <option>in_scope</option>
                    <option>boundary</option>
                    <option>out_of_scope</option>
                  </select>
                </label>
              </div>
              <label className="grid gap-1">
                <span className="font-semibold">分类置信度：{confidence.toFixed(2)}</span>
                <input type="range" min={0} max={1} step={0.05} value={confidence} onChange={(e) => setConfidence(Number(e.target.value))} />
              </label>
              <label className="grid gap-1">
                <span className="font-semibold">红线信号 reason（逗号分隔，可空）</span>
                <input className="h-9 rounded-[4px] border border-[var(--line-strong)] px-2" placeholder="如：是截图,有二维码" value={reason} onChange={(e) => setReason(e.target.value)} />
              </label>
              <div className="grid grid-cols-2 gap-3">
                <label className="grid gap-1">
                  <span className="font-semibold">共性维度 grade：{commonGrade}</span>
                  <input type="range" min={1} max={5} step={1} value={commonGrade} onChange={(e) => setCommonGrade(Number(e.target.value))} />
                </label>
                <label className="grid gap-1">
                  <span className="font-semibold">特有维度 grade：{specificGrade}</span>
                  <input type="range" min={1} max={5} step={1} value={specificGrade} onChange={(e) => setSpecificGrade(Number(e.target.value))} />
                </label>
              </div>
              <Button onClick={runEvaluate} disabled={running || contractQuery.isLoading}>
                <PlayCircle />{running ? "试算中…" : "运行 dry-run 试算"}
              </Button>

              {error && (
                <div className="border border-[#e4b9b6] bg-[#fdf3f2] px-3 py-2 text-[#8d2924]">{error}</div>
              )}

              {result && (
                <div className="mt-1 border-t border-[var(--line)] pt-3">
                  <div className="flex items-center gap-3">
                    <Badge tone={LEVEL_TONE[result.result.level] ?? "neutral"}>{result.result.level}</Badge>
                    <span className="font-data text-2xl font-bold">{result.result.score}</span>
                    {result.result.hard_reject && <Badge tone="warning">红线淘汰</Badge>}
                    {result.resolved && (
                      <span className="text-[var(--muted)]">
                        子类目 {result.resolved.track_key}（{result.resolved.resolved_by}）
                      </span>
                    )}
                  </div>
                  <ol className="mt-3 space-y-1 text-[var(--muted)]">
                    {result.result.steps.map((s, i) => (
                      <li key={i}>
                        <span className="font-data font-semibold text-[var(--fg)]">{s.step}</span>
                        {s.score_after !== null && <span> → {s.score_after}</span>}
                        <span className="ml-1 text-[0.68rem]">{s.note}</span>
                      </li>
                    ))}
                  </ol>
                  {result.result.caps.length > 0 && (
                    <div className="mt-2 text-[0.68rem] text-[#8d4a08]">
                      封顶/压分：{result.result.caps.map((c) => `${c.cap}(${c.reason})`).join("；")}
                    </div>
                  )}
                </div>
              )}
            </div>
          </section>
        </div>
      </div>
    </>
  )
}
