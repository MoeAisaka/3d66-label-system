import { useEffect, useMemo, useState } from "react"
import { ArrowClockwise, ArrowRight, Check, MagicWand, Plus, ShieldCheck, Sparkle, UploadSimple, WarningCircle } from "@phosphor-icons/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { api, jsonBody, promptApi, type PromptPipelineScope } from "@/lib/api"
import type {
  EvaluationCategoryProfile,
  OptimizerConfig,
  PromptOptimizationRun,
  PromptVersion,
  RegressionSummary,
  SampleSetDetail,
  SampleSetSummary,
  StrategyBundleSummary,
} from "@/lib/types"

type RegressionRole = "target_error" | "stable_control" | "blind_holdout"

const pipelineScopeNames: Record<PromptPipelineScope, string> = {
  full_pipeline: "完整流水线专用",
  baseline_regression: "基准回归专用",
  shared: "完整流水线 + 基准回归共用",
}

const regressionRoleNames: Record<RegressionRole, string> = {
  target_error: "目标错例",
  stable_control: "稳定对照",
  blind_holdout: "锁定盲测",
}

export function PromptCandidatesPage() {
  const queryClient = useQueryClient()
  const categories = useQuery({
    queryKey: ["evaluation-categories"],
    queryFn: () => api<{ items: EvaluationCategoryProfile[] }>("/api/evaluation-categories"),
  })
  const [selectedCategoryKey, setSelectedCategoryKey] = useState("space_image")
  const [pipelinePath, setPipelinePath] = useState<"full_pipeline" | "baseline_regression">("full_pipeline")
  const [stageFilter, setStageFilter] = useState<"A" | "B">("A")
  const prompts = useQuery({
    queryKey: ["prompts", selectedCategoryKey],
    queryFn: () => api<{ items: PromptVersion[] }>(`/api/prompts?category_key=${encodeURIComponent(selectedCategoryKey)}`),
  })
  const sampleSets = useQuery({ queryKey: ["sample-sets"], queryFn: () => api<{ items: SampleSetSummary[] }>("/api/sample-sets") })
  const optimizerConfig = useQuery({ queryKey: ["optimizer-config"], queryFn: () => api<OptimizerConfig>("/api/optimizer-config") })
  const optimizations = useQuery({
    queryKey: ["prompt-optimizations"],
    queryFn: () => api<{ items: PromptOptimizationRun[] }>("/api/prompt-optimizations"),
    refetchInterval: (query) => query.state.data?.items.some((item) => ["queued", "running"].includes(item.status)) ? 2500 : false,
  })
  const regressions = useQuery({
    queryKey: ["prompt-regressions"],
    queryFn: () => api<{ items: RegressionSummary[] }>("/api/prompt-regressions"),
    refetchInterval: (query) => query.state.data?.items.some((item) => ["queued", "running"].includes(item.status)) ? 3000 : false,
  })
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const visiblePrompts = useMemo(
    () => (prompts.data?.items ?? []).filter((item) => (
      item.stage === stageFilter
      && (item.pipeline_scope === pipelinePath || item.pipeline_scope === "shared" || !item.pipeline_scope)
    )),
    [pipelinePath, prompts.data?.items, stageFilter],
  )
  const selected = useMemo(
    () => visiblePrompts.find((item) => item.id === selectedId) ?? visiblePrompts[0],
    [selectedId, visiblePrompts],
  )
  const [systemPrompt, setSystemPrompt] = useState("")
  const [userPrompt, setUserPrompt] = useState("")
  const [version, setVersion] = useState("")
  const [changeNote, setChangeNote] = useState("")
  const [pipelineScope, setPipelineScope] = useState<PromptPipelineScope>("shared")
  const [publishScope, setPublishScope] = useState<PromptPipelineScope>("shared")
  const [aiInstruction, setAiInstruction] = useState("")
  const [sampleSetId, setSampleSetId] = useState<number | null>(null)
  const [validationVersion, setValidationVersion] = useState("")
  const [baselineBundleId, setBaselineBundleId] = useState<number | null>(null)
  const [metricRulesVersion, setMetricRulesVersion] = useState("paired-metric-rules-v1")
  const [roleAssignments, setRoleAssignments] = useState<Record<number, RegressionRole | "">>({})
  const activeCategories = (categories.data?.items ?? []).filter((item) => item.status === "active")
  const selectedCategory = activeCategories.find((item) => item.category_key === selectedCategoryKey)
  const filteredSampleSets = (sampleSets.data?.items ?? []).filter(
    (item) => item.category_key === selectedCategoryKey,
  )
  const categoryOptimizations = (optimizations.data?.items ?? []).filter(
    (item) => item.category_key === selectedCategoryKey,
  )

  useEffect(() => {
    if (!activeCategories.length) return
    if (!activeCategories.some((item) => item.category_key === selectedCategoryKey)) {
      setSelectedCategoryKey(activeCategories[0].category_key)
    }
  }, [activeCategories, selectedCategoryKey])

  useEffect(() => {
    setSelectedId(null)
    setPipelineScope(pipelinePath)
    setPublishScope(pipelinePath)
  }, [pipelinePath, stageFilter])

  useEffect(() => {
    if (!selected) return
    setSystemPrompt(selected.system_prompt)
    setUserPrompt(selected.user_prompt)
    setVersion(selected.version)
    setChangeNote("")
    setPipelineScope(selected.pipeline_scope ?? "shared")
    setPublishScope(selected.pipeline_scope ?? "shared")
  }, [selected?.id])

  const promptPayload = () => ({
    category_key: selectedCategoryKey,
    pipeline_scope: pipelineScope,
    stage: selected?.stage ?? "A",
    name: selected?.name ?? "手动提示词",
    version: version.trim(),
    system_prompt: systemPrompt,
    user_prompt: userPrompt,
    rubric_version: selected?.rubric_version ?? "rubric-v2.1",
    change_note: changeNote,
  })
  const createNew = useMutation({
    mutationFn: () => api<{ id: number }>("/api/prompts", {
      method: "POST",
      ...jsonBody({
        category_key: selectedCategoryKey,
        pipeline_scope: pipelineScope,
        stage: stageFilter,
        name: stageFilter === "A" ? "新提示词 A" : "新提示词 B",
        version: `prompt-${Date.now()}`,
        system_prompt: systemPrompt.trim() || "你是一个严格遵循输出合同的评测助手。",
        user_prompt: userPrompt.trim() || "请根据图片完成本次实验评测，并给出你的判断与理由。",
        rubric_version: "rubric-v2.1",
        change_note: changeNote,
        source: "manual",
      }),
    }),
    onSuccess: async (data) => {
      await queryClient.invalidateQueries({ queryKey: ["prompts"] })
      setSelectedId(data.id)
      toast.success("已创建新草稿提示词")
    },
    onError: (error) => toast.error(error.message),
  })
  const save = useMutation({
    mutationFn: () => {
      if (!selected) throw new Error("请先选择提示词版本")
      return promptApi.update(selected.id, promptPayload())
    },
    onSuccess: async (data) => {
      await queryClient.invalidateQueries({ queryKey: ["prompts"] })
      setSelectedId(data.id)
      toast.success("当前草稿版本已保存")
    },
    onError: (error) => toast.error(error.message),
  })
  const saveAs = useMutation({
    mutationFn: () => {
      if (!selected) throw new Error("请先选择提示词版本")
      return promptApi.clone(selected.id, promptPayload())
    },
    onSuccess: async (data) => {
      await queryClient.invalidateQueries({ queryKey: ["prompts"] })
      setSelectedId(data.id)
      toast.success("已另存为新的草稿版本")
    },
    onError: (error) => toast.error(error.message),
  })
  const archive = useMutation({
    mutationFn: () => {
      if (!selected) throw new Error("请先选择提示词版本")
      return promptApi.archive(selected.id)
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["prompts"] })
      setSelectedId(null)
      toast.success("提示词已归档")
    },
    onError: (error) => toast.error(error.message),
  })
  const publish = useMutation({
    mutationFn: () => {
      if (!selected) throw new Error("请先选择提示词版本")
      return promptApi.publish(selected.id, publishScope)
    },
    onSuccess: async (data) => {
      await queryClient.invalidateQueries({ queryKey: ["prompts"] })
      await queryClient.invalidateQueries({ queryKey: ["prompt-regressions"] })
      toast.success(data.regression_run_ids?.length ? `提示词已发布，并启动 ${data.regression_run_ids.length} 组黄金回归` : "提示词已发布")
    },
    onError: (error) => toast.error(error.message),
  })
  const aiRevise = useMutation({
    mutationFn: () =>
      api<{ system_prompt: string; user_prompt: string; change_note: string }>("/api/prompts/ai-revise", {
        method: "POST",
        ...jsonBody({ prompt_id: selected?.id, instruction: aiInstruction }),
      }),
    onSuccess: (data) => {
      setSystemPrompt(data.system_prompt)
      setUserPrompt(data.user_prompt)
      setChangeNote(data.change_note)
      toast.success("AI 已生成修改草案，请人工检查后另存版本")
    },
    onError: (error) => toast.error(error.message),
  })
  const startOptimization = useMutation({
    mutationFn: () => api<{ id: number }>("/api/prompt-optimizations", { method: "POST", ...jsonBody({ prompt_id: selected?.id, sample_set_id: sampleSetId }) }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["prompt-optimizations"] })
      toast.success("已创建样本驱动的提示词优化任务")
    },
    onError: (error) => toast.error(error.message),
  })

  const latestOptimization =
    categoryOptimizations.find((item) => item.id === selected?.source_optimization_run_id) ??
    categoryOptimizations.find((item) => item.base_prompt_id === selected?.id) ??
    categoryOptimizations[0]
  const optimizationSampleSet = useQuery({
    queryKey: ["sample-set", latestOptimization?.sample_set_id],
    queryFn: () => api<SampleSetDetail>(`/api/sample-sets/${latestOptimization?.sample_set_id}`),
    enabled: Boolean(latestOptimization?.id && latestOptimization.status === "completed"),
  })
  const strategyBundles = useQuery({
    queryKey: ["strategy-bundles", latestOptimization?.base_prompt_id],
    queryFn: () => api<{ items: StrategyBundleSummary[] }>(`/api/strategy-bundles?prompt_b_id=${latestOptimization?.base_prompt_id}`),
    enabled: Boolean(latestOptimization?.id && latestOptimization.status === "completed"),
  })
  const materialize = useMutation({
    mutationFn: () => api<{ prompt_id: number; paired_regression_ids: number[] }>(`/api/prompt-optimizations/${latestOptimization?.id}/materialize-and-validate`, {
      method: "POST",
      ...jsonBody({
        version: validationVersion,
        baseline_strategy_bundle_id: baselineBundleId,
        samples: Object.entries(roleAssignments).filter(([, role]) => role).map(([sampleItemId, role]) => ({ sample_item_id: Number(sampleItemId), role })),
        metric_rules_version: metricRulesVersion,
      }),
    }),
    onSuccess: async (data) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["prompts"] }),
        queryClient.invalidateQueries({ queryKey: ["prompt-regressions"] }),
      ])
      setSelectedId(data.prompt_id)
      toast.success(`候选草稿已创建，并启动 ${data.paired_regression_ids.length} 组发布前配对回归`)
    },
    onError: (error) => toast.error(error.message),
  })
  useEffect(() => {
    if (!latestOptimization || latestOptimization.status !== "completed") return
    setValidationVersion(`${latestOptimization.base_prompt_version}-candidate-${latestOptimization.id}`)
    setBaselineBundleId(null)
    setRoleAssignments({})
  }, [latestOptimization?.id, latestOptimization?.status])
  useEffect(() => {
    const firstBundle = strategyBundles.data?.items[0]
    if (firstBundle) setBaselineBundleId((current) => current ?? firstBundle.id)
  }, [strategyBundles.data?.items])
  useEffect(() => {
    const items = optimizationSampleSet.data?.items
    if (!items || !latestOptimization) return
    const plannedItems = Array.isArray(latestOptimization.diagnosis?.sample_policy?.sample_items)
      ? latestOptimization.diagnosis.sample_policy.sample_items as Array<{ sample_item_id: number; role: RegressionRole }>
      : []
    const plannedById = new Map(plannedItems.map((item) => [Number(item.sample_item_id), item.role]))
    const holdoutIds = new Set<number>((latestOptimization.diagnosis?.sample_policy?.blind_holdout_asset_ids ?? []).map(Number))
    setRoleAssignments((current) => {
      const next = { ...current }
      items.forEach((item) => {
        if (plannedById.has(item.id)) next[item.id] = plannedById.get(item.id)!
        else if (holdoutIds.has(item.asset_id)) next[item.id] = "blind_holdout"
        else if (!(item.id in next)) next[item.id] = ""
      })
      return next
    })
  }, [latestOptimization?.id, optimizationSampleSet.data?.items])

  const candidatePrompt = prompts.data?.items.find((item) => item.source_optimization_run_id === latestOptimization?.id)
  const latestPairedRegression = regressions.data?.items.find((item) => item.regression_mode === "paired" && item.trigger_prompt_id === candidatePrompt?.id)
  const selectedPairedRegression = regressions.data?.items.find((item) => item.regression_mode === "paired" && item.trigger_prompt_id === selected?.id)
  const selectedPublishReady = !selected?.source_optimization_run_id || selectedPairedRegression?.approval_status === "approved"
  const roleSet = new Set(Object.values(roleAssignments).filter(Boolean))
  const validationReady = Boolean(
    validationVersion.trim() &&
    baselineBundleId &&
    metricRulesVersion.trim() &&
    roleSet.has("target_error") &&
    roleSet.has("stable_control") &&
    roleSet.has("blind_holdout"),
  )
  function loadCandidate(run: PromptOptimizationRun) {
    setSystemPrompt(run.candidate_system_prompt)
    setUserPrompt(run.candidate_user_prompt)
    setChangeNote(run.change_note)
    setVersion(`${selected?.version || "prompt-b"}-sol-draft`)
    toast.success("候选提示词已载入编辑器，请检查后另存草稿")
  }

  const diagnosis = latestOptimization?.diagnosis ?? {}
  const samplePolicy = diagnosis.sample_policy ?? {}
  const promptChanges = Array.isArray(diagnosis.prompt_changes) ? diagnosis.prompt_changes : []
  const activeOptimization = latestOptimization && ["queued", "running"].includes(latestOptimization.status)
  return (
    <>
      <PageHeader
        index="03.4"
        title="提示词管理器"
        description="按类目、流水线路径和调用 A/B 管理提示词。提示词内容与输出格式完全自由；是否自动转成等级或维度，由基准回归运行时的结果判定方式决定。"
        actions={
          <>
            <select
              aria-label="提示词流水线类目"
              className="h-10 rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm"
              value={selectedCategoryKey}
              onChange={(event) => {
                setSelectedCategoryKey(event.target.value)
                setSelectedId(null)
                setSampleSetId(null)
              }}
            >
              {activeCategories.map((category) => (
                <option key={category.category_key} value={category.category_key}>{category.display_name}</option>
              ))}
            </select>
            <select
              aria-label="提示词流水线路径"
              className="h-10 rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm"
              value={pipelinePath}
              onChange={(event) => setPipelinePath(event.target.value as "full_pipeline" | "baseline_regression")}
            >
              <option value="full_pipeline">完整流水线</option>
              <option value="baseline_regression">基准回归</option>
            </select>
            <select
              aria-label="提示词调用阶段"
              className="h-10 rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm"
              value={stageFilter}
              onChange={(event) => setStageFilter(event.target.value as "A" | "B")}
            >
              <option value="A">调用 A</option>
              <option value="B">调用 B</option>
            </select>
            <Button variant="secondary" onClick={() => prompts.refetch()}><ArrowClockwise />刷新</Button>
            <Button variant="secondary" onClick={() => createNew.mutate()} disabled={createNew.isPending}><Plus />新建提示词</Button>
            <Button onClick={() => saveAs.mutate()} disabled={!selected || !version || saveAs.isPending}><Plus />另存草稿</Button>
          </>
        }
      />
      <div className="mx-auto grid max-w-[1720px] lg:grid-cols-[280px_minmax(0,1fr)]">
        <aside className="border-r border-[var(--line)] bg-white p-4 lg:min-h-[calc(100dvh-125px)]">
          <div className="mb-4 flex items-center justify-between"><h2 className="text-sm font-semibold">版本</h2><span className="font-data text-xs text-[var(--muted)]">{visiblePrompts.length}</span></div>
          <div className="space-y-1">
            {visiblePrompts.map((prompt) => {
              const currentEnabled = prompt.id === (prompt.stage === "A" ? selectedCategory?.prompt_a_id : selectedCategory?.prompt_b_id)
              return (
              <button
                key={prompt.id}
                className={`w-full rounded-[4px] border px-3 py-3 text-left transition-colors ${selected?.id === prompt.id ? "border-[var(--line-strong)] bg-[#f6f9dc]" : "border-transparent hover:bg-[#f8f9f6]"}`}
                onClick={() => setSelectedId(prompt.id)}
              >
                <div className="flex items-center justify-between gap-2"><span className="font-data text-xs font-semibold">调用 {prompt.stage}</span><span className="flex items-center gap-1">{currentEnabled && <Badge tone="success">当前启用</Badge>}<Badge tone={prompt.status === "published" ? "active" : prompt.status === "draft" ? "warning" : "neutral"}>{prompt.status === "published" ? "已发布" : prompt.status === "draft" ? "草稿" : "已归档"}</Badge></span></div>
                <p className="mt-2 truncate text-sm font-semibold">{prompt.version}</p>
                <p className="mt-1 text-[0.68rem] font-semibold text-[var(--muted)]">{pipelineScopeNames[prompt.pipeline_scope ?? "shared"]}</p>
                <p className="mt-1 line-clamp-2 text-xs leading-5 text-[var(--muted)]">{prompt.change_note || prompt.name}</p>
                <p className="font-data mt-2 text-[0.68rem] text-[var(--muted)]">最新更新 {new Date(prompt.updated_at).toLocaleString("zh-CN")}</p>
              </button>
              )
            })}
            {!visiblePrompts.length && <p className="px-3 py-8 text-center text-xs leading-5 text-[var(--muted)]">当前类目、流水线和调用阶段下暂无提示词版本。</p>}
          </div>
        </aside>

        {selected ? (
          <main className="min-w-0 px-5 py-7 md:px-8 lg:px-10 lg:py-9">
            <div className="flex flex-wrap items-start justify-between gap-5 border-b border-[var(--line-strong)] pb-6">
              <div><p className="font-data text-xs text-[var(--muted)]">调用 {selected.stage} · {selected.rubric_version}</p><h2 className="font-editorial mt-2 text-3xl font-bold">{selected.name}</h2><p className="mt-2 text-sm text-[var(--muted)]">当前选择：{selected.version}，创建者 {selected.created_by}</p><p className="mt-1 text-xs font-semibold text-[var(--muted)]">{pipelineScopeNames[selected.pipeline_scope ?? "shared"]}</p><p className="font-data mt-1 text-xs text-[var(--muted)]">最新更新时间：{new Date(selected.updated_at).toLocaleString("zh-CN")}</p></div>
              <div className="flex flex-wrap items-end justify-end gap-2 text-right">
                {selected.status !== "published" && <>
                  <label className="text-left"><span className="mb-1 block text-[0.68rem] font-semibold text-[var(--muted)]">发布范围</span><select className="h-9 rounded-[4px] border border-[var(--line-strong)] bg-white px-2 text-xs" value={publishScope} onChange={(event) => setPublishScope(event.target.value as PromptPipelineScope)}>{(Object.keys(pipelineScopeNames) as PromptPipelineScope[]).map((scope) => <option key={scope} value={scope}>{pipelineScopeNames[scope]}</option>)}</select></label>
                  <Button onClick={() => publish.mutate()} disabled={publish.isPending || !selectedPublishReady}><UploadSimple />发布</Button>
                </>}
                <Button variant="secondary" onClick={() => archive.mutate()} disabled={archive.isPending || selected.status === "archived"}>归档</Button>
                {selected.source_optimization_run_id && <p className="basis-full max-w-64 text-xs text-[var(--muted)]">{selectedPublishReady ? "配对回归与人工批准已通过，可以发布。" : "需先通过发布前配对回归并完成人工批准。"}</p>}
              </div>
            </div>

            <section className="mt-7 border-y border-[var(--line-strong)] bg-white">
              <div className="grid gap-5 p-5 xl:grid-cols-[minmax(0,1fr)_240px_auto] xl:items-end">
                <div>
                  <div className="flex items-center gap-2"><Sparkle size={20} weight="fill" /><h3 className="font-semibold">从人工校验样本生成候选提示词</h3></div>
                  <p className="mt-2 max-w-[72ch] text-xs leading-5 text-[var(--muted)]">提示词诊断模型会读取样本中的维度纠错、原因和图片，保留一部分图片作为盲测，不会直接改动当前提示词。</p>
                </div>
                <label><span className="mb-2 block text-xs font-semibold">选择校验样本集</span><select className="h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm" value={sampleSetId ?? ""} onChange={(event) => setSampleSetId(event.target.value ? Number(event.target.value) : null)}><option value="">请选择样本集</option>{filteredSampleSets.map((set) => <option key={set.id} value={set.id}>{set.name} · {set.item_count}张</option>)}</select></label>
                <Button onClick={() => startOptimization.mutate()} disabled={!sampleSetId || selected.stage !== "B" || !optimizerConfig.data?.has_api_key || Boolean(activeOptimization) || startOptimization.isPending}>{activeOptimization ? "诊断模型正在分析" : "生成候选提示词"}<MagicWand /></Button>
              </div>
              {!optimizerConfig.data?.has_api_key && <div className="flex items-start gap-2 border-t border-[var(--line)] bg-[#fff9ef] px-5 py-3 text-xs leading-5 text-[#7d4308]"><WarningCircle className="mt-0.5 shrink-0" />请先到“模型配置”填写提示词诊断模型 API Key。网页端登录权限不能直接供网站调用。</div>}
              {selected.stage !== "B" && <div className="border-t border-[var(--line)] px-5 py-3 text-xs text-[var(--muted)]">样本驱动优化目前用于调用 B 的维度评分。请选择调用 B 的提示词版本。</div>}
              {latestOptimization && <div className="border-t border-[var(--line)] px-5 py-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div><p className="text-sm font-semibold">最近一次：{latestOptimization.sample_set_name}</p><p className="font-data mt-1 text-xs text-[var(--muted)]">{latestOptimization.optimizer_model_id} · {latestOptimization.sample_count || "—"} 张已校验样本</p></div>
                  <Badge tone={latestOptimization.status === "completed" ? "success" : latestOptimization.status === "failed" ? "warning" : "active"}>{latestOptimization.status === "completed" ? "候选已生成" : latestOptimization.status === "failed" ? "生成失败" : `分析中 ${latestOptimization.progress}%`}</Badge>
                </div>
                {activeOptimization && <div className="mt-4 h-1.5 overflow-hidden bg-[#edf0e9]"><div className="h-full bg-primary transition-[width] duration-200" style={{ width: `${latestOptimization.progress}%` }} /></div>}
                {latestOptimization.error_message && <p className="mt-3 text-xs leading-5 text-[#8d2924]">{latestOptimization.error_message}</p>}
                {latestOptimization.status === "completed" && <div className="mt-4 grid gap-4 xl:grid-cols-[220px_1fr_auto] xl:items-start">
                  <div className="grid grid-cols-2 gap-px border border-[var(--line)] bg-[var(--line)] text-center"><div className="bg-white px-3 py-3"><p className="font-data text-xl font-semibold">{samplePolicy.analysis_count ?? "—"}</p><p className="mt-1 text-[0.68rem] text-[var(--muted)]">参与诊断</p></div><div className="bg-white px-3 py-3"><p className="font-data text-xl font-semibold">{samplePolicy.blind_holdout_count ?? 0}</p><p className="mt-1 text-[0.68rem] text-[var(--muted)]">保留盲测</p></div></div>
                  <div><p className="text-xs font-semibold">建议修改 {promptChanges.length} 处</p><div className="mt-2 space-y-2">{promptChanges.slice(0, 4).map((change: any, index: number) => <div key={index} className="border-t border-[var(--line)] pt-2 text-xs leading-5"><span className="font-semibold">{change.section || "提示词规则"}</span><span className="ml-2 text-[var(--muted)]">{change.reason || change.operation || "根据人工纠错样本调整"}</span></div>)}</div></div>
                  <Button onClick={() => loadCandidate(latestOptimization)}>载入候选内容</Button>
                </div>}
              </div>}
              {latestOptimization?.status === "completed" && (
                <div className="border-t border-[var(--line-strong)] px-5 py-5">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div><div className="flex items-center gap-2"><ShieldCheck /><h3 className="font-semibold">候选物化与回归交接</h3></div><p className="mt-2 max-w-[78ch] text-xs leading-5 text-[var(--muted)]">候选会先保存为不可变草稿，并以同一模型、A 提示词、评分规则和样本快照与基线配对比较。三类样本缺一不可；创建后请前往“小样本配对回归”查看证据。</p></div>
                    {candidatePrompt && <Badge tone="success">候选草稿已冻结</Badge>}
                  </div>

                  {!candidatePrompt && <div className="mt-5 grid gap-4 lg:grid-cols-3">
                    <label><span className="mb-2 block text-xs font-semibold">候选版本号</span><Input value={validationVersion} onChange={(event) => setValidationVersion(event.target.value)} /></label>
                    <label><span className="mb-2 block text-xs font-semibold">基线策略快照</span><select className="h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm" value={baselineBundleId ?? ""} onChange={(event) => setBaselineBundleId(event.target.value ? Number(event.target.value) : null)}><option value="">请选择基线</option>{strategyBundles.data?.items.map((bundle) => <option key={bundle.id} value={bundle.id}>#{bundle.id} · {bundle.model_id} · A {bundle.prompt_a_version} · B {bundle.prompt_b_version}</option>)}</select></label>
                    <label><span className="mb-2 block text-xs font-semibold">指标规则版本</span><Input value={metricRulesVersion} onChange={(event) => setMetricRulesVersion(event.target.value)} /></label>
                  </div>}
                  {!candidatePrompt && strategyBundles.isSuccess && !strategyBundles.data.items.length && <p className="mt-3 text-xs text-[#8d2924]">没有找到与原提示词一致的可复现基线策略。请先用当前发布版本完成至少一次正式评测。</p>}

                  {!candidatePrompt && <div className="mt-5 border-y border-[var(--line)]">
                    <div className="grid grid-cols-[64px_minmax(0,1fr)_190px] gap-3 bg-[#fafbf8] px-3 py-2 text-xs font-semibold text-[var(--muted)]"><span>图片</span><span>冻结样本</span><span>回归角色</span></div>
                    <div className="max-h-80 overflow-y-auto">{optimizationSampleSet.data?.items.map((item) => <div key={item.id} className="grid grid-cols-[64px_minmax(0,1fr)_190px] items-center gap-3 border-t border-[var(--line)] px-3 py-2"><img src={item.image_url} alt="" className="size-14 rounded-[4px] border border-[var(--line)] object-cover" /><div className="min-w-0"><p className="file-name truncate text-sm">{item.asset_name}</p><p className="font-data mt-1 text-[0.68rem] text-[var(--muted)]">样本 #{item.id} · 标准 V{item.truth_revision}</p></div><select aria-label={`${item.asset_name}回归角色`} className="h-9 rounded-[4px] border border-[var(--line-strong)] bg-white px-2 text-xs" value={roleAssignments[item.id] || ""} onChange={(event) => setRoleAssignments((current) => ({ ...current, [item.id]: event.target.value as RegressionRole | "" }))}><option value="">不纳入本轮</option>{(Object.keys(regressionRoleNames) as RegressionRole[]).map((role) => <option key={role} value={role}>{regressionRoleNames[role]}</option>)}</select></div>)}</div>
                  </div>}
                  {!candidatePrompt && <div className="mt-4 flex flex-wrap items-center justify-between gap-3"><p className="text-xs text-[var(--muted)]">已选择：目标错例 {Object.values(roleAssignments).filter((role) => role === "target_error").length} · 稳定对照 {Object.values(roleAssignments).filter((role) => role === "stable_control").length} · 锁定盲测 {Object.values(roleAssignments).filter((role) => role === "blind_holdout").length}</p><Button onClick={() => materialize.mutate()} disabled={!validationReady || materialize.isPending}>创建候选并启动配对回归<ShieldCheck /></Button></div>}

                  {candidatePrompt && !latestPairedRegression && <p className="mt-4 text-xs text-[var(--muted)]">候选草稿已创建，正在读取配对回归任务。</p>}
                  {latestPairedRegression && <div className="mt-5 flex flex-wrap items-center justify-between gap-4 border-y border-[var(--line)] bg-[#fafbf8] p-4">
                    <div><p className="font-semibold">{latestPairedRegression.name}</p><p className="font-data mt-1 text-xs text-[var(--muted)]">回归 #{latestPairedRegression.id} · {latestPairedRegression.completed}/{latestPairedRegression.total} 已完成 · 候选提示词 #{candidatePrompt?.id}</p></div>
                    <Button asChild><Link to={`/workflow/optimization/paired-regression?run=${latestPairedRegression.id}`}>查看配对回归证据<ArrowRight /></Link></Button>
                  </div>}
                </div>
              )}
            </section>

            <section className="mt-7 grid gap-4 border-y border-[var(--line-strong)] bg-white p-4 xl:grid-cols-[minmax(0,1fr)_280px] xl:p-5">
              <div>
                <div className="flex items-center gap-2"><MagicWand size={20} /><h3 className="font-semibold">让 AI 提议修改</h3></div>
                <p className="mt-1 text-xs leading-5 text-[var(--muted)]">AI 返回的内容只会进入下方编辑器，不写入数据库，也不会发布。</p>
                <Textarea className="mt-3 min-h-24" value={aiInstruction} onChange={(event) => setAiInstruction(event.target.value)} placeholder="例如：保留输出结构，增强对局部空间和艺术性景深的容错说明" />
              </div>
              <div className="flex items-end"><Button className="w-full" onClick={() => aiRevise.mutate()} disabled={!aiInstruction || aiRevise.isPending}>{aiRevise.isPending ? "AI 正在生成草案" : "生成修改草案"}<MagicWand /></Button></div>
            </section>

            <div className="mt-8 border-l-2 border-primary bg-[#f8faed] px-4 py-3 text-xs leading-5">
              <strong>提示词不受固定输出协议约束。</strong> 可以要求自然语言、任意 JSON、自定义维度或完全不同的评测方法。选择“自由实验”运行基准回归时，即使没有 L1–L5、范围字段或八维，任务也会正常完成并保留原始回答。
            </div>

            <div className="mt-5 grid gap-7 2xl:grid-cols-2">
              <label className="block"><span className="mb-2 flex items-center justify-between text-sm font-semibold"><span>System Prompt</span><span className="font-data text-xs font-normal text-[var(--muted)]">{systemPrompt.length} 字符</span></span><Textarea className="min-h-[520px] font-mono text-[0.78rem] leading-6" value={systemPrompt} onChange={(event) => setSystemPrompt(event.target.value)} /></label>
              <label className="block"><span className="mb-2 flex items-center justify-between text-sm font-semibold"><span>User Prompt</span><span className="font-data text-xs font-normal text-[var(--muted)]">{userPrompt.length} 字符</span></span><Textarea className="min-h-[520px] font-mono text-[0.78rem] leading-6" value={userPrompt} onChange={(event) => setUserPrompt(event.target.value)} /></label>
            </div>

            <section className="mt-8 border-y border-[var(--line-strong)] bg-white p-5">
              <h3 className="font-editorial text-xl font-bold">保存当前版本</h3>
              <div className="mt-4 grid gap-4 lg:grid-cols-[220px_220px_minmax(220px,1fr)_auto]">
                <label><span className="mb-2 block text-xs font-semibold">版本号</span><Input value={version} onChange={(event) => setVersion(event.target.value)} /></label>
                <label><span className="mb-2 block text-xs font-semibold">流水线归属</span><select className="h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm" value={pipelineScope} onChange={(event) => setPipelineScope(event.target.value as PromptPipelineScope)}>{(Object.keys(pipelineScopeNames) as PromptPipelineScope[]).map((scope) => <option key={scope} value={scope}>{pipelineScopeNames[scope]}</option>)}</select></label>
                <label><span className="mb-2 block text-xs font-semibold">修改说明</span><Input value={changeNote} onChange={(event) => setChangeNote(event.target.value)} placeholder="说明修改目的和预期影响" /></label>
                <div className="flex items-end gap-2"><Button onClick={() => save.mutate()} disabled={!version || selected.status !== "draft" || save.isPending}><Check />保存当前版本</Button><Button variant="secondary" onClick={() => saveAs.mutate()} disabled={!version || saveAs.isPending}><Plus />另存为草稿</Button></div>
              </div>
              {selected.status === "published" && <p className="mt-3 text-xs leading-5 text-[var(--muted)]">已发布版本不可原地修改；编辑后请使用“另存为草稿”，再发布到目标流水线。</p>}
            </section>
          </main>
        ) : <div className="min-h-[60dvh] animate-pulse bg-white" />}
      </div>
    </>
  )
}
