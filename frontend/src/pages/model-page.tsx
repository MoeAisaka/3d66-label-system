import { useEffect, useState, type ReactNode } from "react"
import { CheckCircle, FloppyDisk, Key, PlugsConnected, Plus, SlidersHorizontal, WarningCircle } from "@phosphor-icons/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api, jsonBody } from "@/lib/api"
import type { EvaluationCategoryProfile, ModelConfig, OptimizerConfig, PromptVersion, ReviewWorkflowPolicy, SamplingPolicy, User } from "@/lib/types"

type FormState = Omit<ModelConfig, "id" | "api_key_mask" | "updated_at" | "has_api_key" | "active"> & { api_key: string }
type OptimizerFormState = Omit<OptimizerConfig, "id" | "api_key_mask" | "updated_at" | "has_api_key"> & { api_key: string }
type BenchmarkConfigDraft = Omit<FormState, "benchmark_enabled"> & {
  provider: string
  benchmark_enabled: boolean
}

type CategoryPromptMode = "follow" | "single" | "ab"

type CategoryDraft = EvaluationCategoryProfile & {
  api_key?: string
  prompt_mode: CategoryPromptMode
}

function categoryDraft(profile: EvaluationCategoryProfile): CategoryDraft {
  return {
    ...profile,
    prompt_mode: profile.prompt_a_id === null
      ? "follow"
      : profile.prompt_b_id === null
        ? "single"
        : "ab",
  }
}

const emptyBenchmarkConfig: BenchmarkConfigDraft = {
  provider: "openai",
  name: "",
  base_url: "",
  api_path: "/chat/completions",
  model_id: "",
  api_key: "",
  temperature: 0.1,
  max_tokens: 4096,
  timeout_seconds: 120,
  max_retries: 1,
  max_concurrency: 1,
  structured_output: true,
  high_risk_review_enabled: false,
  input_micros_per_million_tokens: 0,
  output_micros_per_million_tokens: 0,
  max_input_tokens: 0,
  benchmark_enabled: false,
}

export function ModelPage() {
  const queryClient = useQueryClient()
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<User>("/api/auth/me") })
  const config = useQuery({ queryKey: ["model-config"], queryFn: () => api<ModelConfig>("/api/model-config") })
  const modelConfigs = useQuery({ queryKey: ["model-configs"], queryFn: () => api<{ items: ModelConfig[] }>("/api/model-configs") })
  const optimizerConfig = useQuery({ queryKey: ["optimizer-config"], queryFn: () => api<OptimizerConfig>("/api/optimizer-config") })
  const samplingPolicy = useQuery({ queryKey: ["sampling-policy"], queryFn: () => api<SamplingPolicy>("/api/sampling-policy") })
  const reviewWorkflowPolicy = useQuery({ queryKey: ["review-workflow-policy"], queryFn: () => api<ReviewWorkflowPolicy>("/api/review-workflow-policy") })
  const categoryProfiles = useQuery({ queryKey: ["evaluation-categories"], queryFn: () => api<{ items: EvaluationCategoryProfile[] }>("/api/evaluation-categories") })
  const prompts = useQuery({ queryKey: ["prompts"], queryFn: () => api<{ items: PromptVersion[] }>("/api/prompts") })
  const [form, setForm] = useState<FormState | null>(null)
  const [optimizerForm, setOptimizerForm] = useState<OptimizerFormState | null>(null)
  const [samplingForm, setSamplingForm] = useState<SamplingPolicy | null>(null)
  const [reviewWorkflowForm, setReviewWorkflowForm] = useState<ReviewWorkflowPolicy | null>(null)
  const [benchmarkForm, setBenchmarkForm] = useState<BenchmarkConfigDraft>(emptyBenchmarkConfig)
  const [mainBenchmarkConfirmed, setMainBenchmarkConfirmed] = useState(false)
  const [benchmarkCreateConfirmed, setBenchmarkCreateConfirmed] = useState(false)
  const [categoryDrafts, setCategoryDrafts] = useState<Record<string, CategoryDraft>>({})
  useEffect(() => {
    if (!config.data) return
    const { id: _id, api_key_mask: _mask, updated_at: _updated, has_api_key: _hasApiKey, active: _active, ...rest } = config.data
    setForm({ ...rest, api_key: "" })
  }, [config.data])
  useEffect(() => {
    if (!optimizerConfig.data) return
    const { id: _id, api_key_mask: _mask, updated_at: _updated, has_api_key: _hasApiKey, ...rest } = optimizerConfig.data
    setOptimizerForm({ ...rest, api_key: "" })
  }, [optimizerConfig.data])
  useEffect(() => {
    if (samplingPolicy.data) setSamplingForm(samplingPolicy.data)
  }, [samplingPolicy.data])
  useEffect(() => {
    if (reviewWorkflowPolicy.data) setReviewWorkflowForm(reviewWorkflowPolicy.data)
  }, [reviewWorkflowPolicy.data])
  useEffect(() => {
    if (!categoryProfiles.data) return
    setCategoryDrafts(Object.fromEntries(categoryProfiles.data.items.map((item) => [item.category_key, categoryDraft(item)])))
  }, [categoryProfiles.data])

  const save = useMutation({
    mutationFn: () => api("/api/model-config", { method: "PUT", ...jsonBody(form) }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["model-config"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
      ])
      setForm((current) => current ? { ...current, api_key: "" } : current)
      setMainBenchmarkConfirmed(false)
      toast.success("模型配置已保存")
    },
    onError: (error) => toast.error(error.message),
  })
  const test = useMutation({
    mutationFn: () => api<{ ok: boolean; message: string }>("/api/model-config/test", { method: "POST" }),
    onSuccess: (data) => toast.success(data.message || "连接成功"),
    onError: (error) => toast.error(error.message),
  })
  const saveOptimizer = useMutation({
    mutationFn: () => api("/api/optimizer-config", { method: "PUT", ...jsonBody(optimizerForm) }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["optimizer-config"] })
      setOptimizerForm((current) => current ? { ...current, api_key: "" } : current)
      toast.success("提示词诊断模型配置已保存")
    },
    onError: (error) => toast.error(error.message),
  })
  const testOptimizer = useMutation({
    mutationFn: () => api<{ ok: boolean; message: string }>("/api/optimizer-config/test", { method: "POST" }),
    onSuccess: (data) => toast.success(data.message || "诊断模型连接成功"),
    onError: (error) => toast.error(error.message),
  })
  const createBenchmarkConfig = useMutation({
    mutationFn: () => api<ModelConfig>("/api/model-configs", {
      method: "POST",
      ...jsonBody(benchmarkForm),
    }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["model-configs"] })
      setBenchmarkForm(emptyBenchmarkConfig)
      setBenchmarkCreateConfirmed(false)
      toast.success("横评模型配置已创建")
    },
    onError: (error) => toast.error(error.message),
  })
  const saveSampling = useMutation({
    mutationFn: () => api<SamplingPolicy>("/api/sampling-policy", { method: "PUT", ...jsonBody(samplingForm) }),
    onSuccess: async (data) => {
      setSamplingForm(data)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["sampling-policy"] }),
        queryClient.invalidateQueries({ queryKey: ["evaluations"] }),
      ])
      toast.success(`抽样策略已保存为 ${data.version}`)
    },
    onError: (error) => toast.error(error.message),
  })
  const saveReviewWorkflow = useMutation({
    mutationFn: () => api<ReviewWorkflowPolicy>("/api/review-workflow-policy", {
      method: "PUT",
      ...jsonBody({ initial_reviewers: reviewWorkflowForm?.initial_reviewers }),
    }),
    onSuccess: async (data) => {
      setReviewWorkflowForm(data)
      await queryClient.invalidateQueries({ queryKey: ["review-workflow-policy"] })
      toast.success(`初审工作流已保存为 ${data.version}`)
    },
    onError: (error) => toast.error(error.message),
  })
  const saveCategory = useMutation({
    mutationFn: (draft: CategoryDraft) => api<EvaluationCategoryProfile>(`/api/evaluation-categories/${draft.category_key}`, {
      method: "PUT",
      ...jsonBody({
        display_name: draft.display_name,
        status: draft.status,
        allowed_mime_types: draft.allowed_mime_types,
        preprocess_config: draft.preprocess_config,
        prompt_a_id: draft.prompt_mode === "follow" ? null : draft.prompt_a_id,
        prompt_b_id: draft.prompt_mode === "ab" ? draft.prompt_b_id : null,
        model_config_id: draft.model_config_id,
        rubric_version: draft.rubric_version,
        dimension_schema_key: draft.dimension_schema_key,
        dimension_schema_version: draft.dimension_schema_version,
      }),
    }),
    onSuccess: async (data) => {
      setCategoryDrafts((current) => ({ ...current, [data.category_key]: categoryDraft(data) }))
      await categoryProfiles.refetch()
      toast.success(`${data.display_name}类目配置已保存`)
    },
    onError: (error) => toast.error(error.message),
  })

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((current) => current ? { ...current, [key]: value } : current)
  }

  function updateOptimizer<K extends keyof OptimizerFormState>(key: K, value: OptimizerFormState[K]) {
    setOptimizerForm((current) => current ? { ...current, [key]: value } : current)
  }

  function updateSampling<K extends keyof SamplingPolicy>(key: K, value: SamplingPolicy[K]) {
    setSamplingForm((current) => current ? { ...current, [key]: value } : current)
  }

  function updateReviewWorkflow(initialReviewers: 1 | 3 | 5 | 7 | 9) {
    setReviewWorkflowForm((current) => current ? { ...current, initial_reviewers: initialReviewers } : current)
  }

  function updateBenchmark<K extends keyof BenchmarkConfigDraft>(key: K, value: BenchmarkConfigDraft[K]) {
    setBenchmarkForm((current) => ({ ...current, [key]: value }))
  }

  function updateCategory(categoryKey: string, patch: Partial<CategoryDraft>) {
    setCategoryDrafts((current) => ({
      ...current,
      [categoryKey]: { ...current[categoryKey], ...patch },
    }))
  }

  if (me.data && !me.data.is_admin) {
    return (
      <>
        <PageHeader index="06" title="模型配置" description="模型端点、计价与密钥只能由管理员修改；当前账号仅可查看安全状态。" />
        <div className="mx-auto max-w-[1180px] px-5 py-8 md:px-8 lg:px-10">
          <div className="border-y border-[var(--line-strong)] bg-white px-5 py-5 text-sm text-[var(--muted)]">当前账号无配置权限。API Key、完整凭据引用和原始连接异常不会显示在页面中。</div>
          <div className="mt-6 divide-y divide-[var(--line)] border-y border-[var(--line-strong)] bg-white">{(modelConfigs.data?.items ?? []).map((item) => <div key={item.id} className="grid gap-2 px-5 py-4 md:grid-cols-[1fr_auto_auto]"><div><strong>{item.name}</strong><p className="font-data mt-1 text-xs text-[var(--muted)]">{item.model_id}</p></div><Badge>{item.has_api_key ? "密钥已配置" : "未配置密钥"}</Badge><Badge tone={item.benchmark_enabled ? "warning" : "neutral"}>{item.benchmark_enabled ? "横评已启用" : "横评关闭"}</Badge></div>)}</div>
        </div>
      </>
    )
  }

  return (
    <>
      <PageHeader
        index="06"
        title="模型配置"
        description="主评测模型、提示词优化模型与横评模型分别配置。API Key 仅写入当前电脑的系统凭据库，页面和 API 不返回明文。"
        actions={
          <>
            <Button variant="secondary" onClick={() => test.mutate()} disabled={!config.data?.has_api_key || test.isPending}><PlugsConnected />测试主模型连接</Button>
            <Button onClick={() => save.mutate()} disabled={!form || save.isPending || (form.benchmark_enabled && !mainBenchmarkConfirmed)}><FloppyDisk />保存主模型配置</Button>
          </>
        }
      />
      <div className="mx-auto max-w-[1180px] px-5 py-7 md:px-8 lg:px-10 lg:py-10">
        <section className="grid gap-7 border-y border-[var(--line-strong)] bg-white px-5 py-6 lg:grid-cols-[230px_1fr] lg:px-7">
          <div>
            <div className="flex size-10 items-center justify-center rounded-[4px] bg-primary"><PlugsConnected size={21} weight="bold" /></div>
            <h2 className="font-editorial mt-5 text-2xl font-bold">连接信息</h2>
            <p className="mt-2 text-sm leading-6 text-[var(--muted)]">支持 OpenAI-compatible Chat Completions 接口。可配置任意兼容渠道的 Base URL、路径和模型 ID，火山方舟不再是固定前提。</p>
          </div>
          {form ? (
            <div className="grid gap-5 md:grid-cols-2">
              <Field label="配置名称"><Input value={form.name} onChange={(event) => update("name", event.target.value)} /></Field>
              <Field label="渠道标识"><Input value={form.provider} onChange={(event) => update("provider", event.target.value)} placeholder="doubao / openai / deepseek" /></Field>
              <Field label="模型 / 端点 ID"><Input value={form.model_id} onChange={(event) => update("model_id", event.target.value)} /></Field>
              <Field label="Base URL"><Input value={form.base_url} onChange={(event) => update("base_url", event.target.value)} /></Field>
              <Field label="API 路径"><Input value={form.api_path} onChange={(event) => update("api_path", event.target.value)} /></Field>
            </div>
          ) : <div className="h-40 animate-pulse bg-[#f1f3ef]" />}
        </section>

        <section className="mt-8 grid gap-7 border-y border-[var(--line-strong)] bg-white px-5 py-6 lg:grid-cols-[230px_1fr] lg:px-7">
          <div>
            <div className="flex size-10 items-center justify-center rounded-[4px] border border-[var(--line-strong)]"><Key size={21} /></div>
            <h2 className="font-editorial mt-5 text-2xl font-bold">API Key</h2>
            <p className="mt-2 text-sm leading-6 text-[var(--muted)]">保存后不再返回完整密钥。留空表示保留原密钥，更换电脑时需要重新填写。</p>
          </div>
          <div>
            <div className="mb-4 flex items-center gap-3">
              {config.data?.has_api_key ? <CheckCircle size={22} weight="fill" className="text-[#2f6f48]" /> : <WarningCircle size={22} className="text-[#a85a0a]" />}
              <Badge tone={config.data?.has_api_key ? "success" : "warning"}>{config.data?.has_api_key ? "当前电脑已保存密钥" : "尚未保存密钥"}</Badge>
            </div>
            <Field label="输入新的 API Key"><Input type="password" value={form?.api_key ?? ""} onChange={(event) => update("api_key", event.target.value)} placeholder={config.data?.has_api_key ? "留空以保留当前密钥" : "请输入当前渠道 API Key"} autoComplete="new-password" /></Field>
          </div>
        </section>

        <section className="mt-8 border-y border-[var(--line-strong)] bg-white">
          <div className="grid gap-7 border-b border-[var(--line)] px-5 py-6 lg:grid-cols-[230px_1fr] lg:px-7">
            <div>
              <div className="flex size-10 items-center justify-center rounded-[4px] border border-[var(--line-strong)]"><SlidersHorizontal size={21} /></div>
              <h2 className="font-editorial mt-5 text-2xl font-bold">评测类目配置</h2>
              <p className="mt-2 text-sm leading-6 text-[var(--muted)]">每个类目独立绑定提示词、模型和前处理规则。保存后只影响新建任务，历史任务继续使用自己的冻结快照。</p>
            </div>
            <div className="space-y-5">
              {(categoryProfiles.data?.items ?? []).map((profile) => {
                const draft = categoryDrafts[profile.category_key]
                if (!draft) return null
                const categoryPrompts = (prompts.data?.items ?? []).filter((item) => item.status === "published" && item.rubric_version === draft.rubric_version)
                const preprocess = draft.preprocess_config
                const promptReady = draft.prompt_mode === "follow"
                  ? draft.category_key === "space_image"
                  : draft.prompt_a_id !== null && (draft.prompt_mode === "single" || draft.prompt_b_id !== null)
                const canSave = draft.status !== "active" || promptReady
                return (
                  <div key={profile.category_key} className="border border-[var(--line)] bg-[#fafbf8] p-4">
                    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                      <div><div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold">{draft.display_name}</h3><Badge tone={draft.status === "active" ? "success" : draft.status === "retired" ? "neutral" : "warning"}>{draft.status === "active" ? "已启用" : draft.status === "retired" ? "已停用" : "草稿"}</Badge></div><p className="font-data mt-1 text-xs text-[var(--muted)]">{draft.category_key} · MIME {draft.allowed_mime_types.join(", ")} · {draft.rubric_version}</p></div>
                      <Button size="sm" onClick={() => saveCategory.mutate(draft)} disabled={saveCategory.isPending || !canSave}><FloppyDisk />保存类目</Button>
                    </div>
                    <div className="grid gap-4 md:grid-cols-2">
                      <Field label="显示名称"><Input value={draft.display_name} onChange={(event) => updateCategory(draft.category_key, { display_name: event.target.value })} /></Field>
                      <Field label="运行状态"><select className="flex h-11 w-full rounded-[4px] border border-input bg-transparent px-3 text-sm" value={draft.status} onChange={(event) => updateCategory(draft.category_key, { status: event.target.value as CategoryDraft["status"] })}><option value="active">启用新任务</option><option value="draft">草稿，不接收任务</option><option value="retired">停用，不接收任务</option></select></Field>
                      <Field label="绑定主模型"><select className="flex h-11 w-full rounded-[4px] border border-input bg-transparent px-3 text-sm" value={draft.model_config_id ?? ""} onChange={(event) => updateCategory(draft.category_key, { model_config_id: event.target.value ? Number(event.target.value) : null })}><option value="">跟随主模型</option>{(modelConfigs.data?.items ?? []).map((item) => <option key={item.id} value={item.id}>{item.name} · {item.model_id}</option>)}</select></Field>
                      <div className="md:col-span-2"><span className="mb-2 block text-xs font-semibold">提示词调用模式</span><div className="inline-flex max-w-full overflow-auto rounded-[4px] border border-[var(--line-strong)] bg-white p-1">{([...(draft.category_key === "space_image" ? [{ value: "follow", label: "跟随任务" }] : []), { value: "single", label: "单提示词" }, { value: "ab", label: "A/B 两段" }] as Array<{ value: CategoryPromptMode; label: string }>).map((option) => <button type="button" key={option.value} onClick={() => updateCategory(draft.category_key, { prompt_mode: option.value, ...(option.value === "follow" ? { prompt_a_id: null, prompt_b_id: null } : option.value === "single" ? { prompt_b_id: null } : {}) })} className={`h-9 whitespace-nowrap rounded-[3px] px-3 text-xs font-semibold ${draft.prompt_mode === option.value ? "bg-[#11130f] text-white" : "text-[var(--muted)] hover:bg-[#f1f3ef]"}`}>{option.label}</button>)}</div></div>
                      {draft.prompt_mode !== "follow" && <Field label={draft.prompt_mode === "single" ? "单提示词版本" : "A 阶段版本"}><select className="flex h-11 w-full rounded-[4px] border border-input bg-transparent px-3 text-sm" value={draft.prompt_a_id ?? ""} onChange={(event) => updateCategory(draft.category_key, { prompt_a_id: event.target.value ? Number(event.target.value) : null })}><option value="">请选择 {draft.rubric_version} 版本</option>{categoryPrompts.filter((item) => item.stage === "A").map((item) => <option key={item.id} value={item.id}>{item.version} · {item.name}</option>)}</select></Field>}
                      {draft.prompt_mode === "ab" && <Field label="B 阶段版本"><select className="flex h-11 w-full rounded-[4px] border border-input bg-transparent px-3 text-sm" value={draft.prompt_b_id ?? ""} onChange={(event) => updateCategory(draft.category_key, { prompt_b_id: event.target.value ? Number(event.target.value) : null })}><option value="">请选择 {draft.rubric_version} 版本</option>{categoryPrompts.filter((item) => item.stage === "B").map((item) => <option key={item.id} value={item.id}>{item.version} · {item.name}</option>)}</select></Field>}
                      {draft.category_key === "pdf_text" && <><Field label="最多处理页数"><Input type="number" min="1" max="20" value={Number(preprocess.max_pages ?? 4)} onChange={(event) => updateCategory(draft.category_key, { preprocess_config: { ...preprocess, max_pages: Number(event.target.value) } })} /></Field><Field label="文本上限字符数"><Input type="number" min="1000" max="100000" value={Number(preprocess.max_text_chars ?? 24000)} onChange={(event) => updateCategory(draft.category_key, { preprocess_config: { ...preprocess, max_text_chars: Number(event.target.value) } })} /></Field></>}
                      {draft.category_key === "pdf_text" && <div className="border-y border-[#b9cddd] bg-[#f5f8fb] px-4 py-3 text-xs leading-5 text-[#355369] md:col-span-2">固定流程：文本抽取 → 必要时 OCR → 页图接触表 → 多模态总结 → 类目评测。多模态总结属于必经前置步骤。</div>}
                      {draft.category_key === "material_image" && <label className="flex min-h-20 items-center justify-between gap-4 border border-[var(--line)] bg-white px-4"><span><span className="block text-sm font-semibold">材质专项关注</span><span className="mt-1 block text-xs text-[var(--muted)]">提示模型优先判断纹理、反光、接缝和工艺真实性。</span></span><input type="checkbox" checked={Boolean(preprocess.material_focus)} onChange={(event) => updateCategory(draft.category_key, { preprocess_config: { ...preprocess, material_focus: event.target.checked } })} className="size-5 accent-[#11130f]" /></label>}
                    </div>
                    {!canSave && <p className="mt-3 border-t border-[#e8c876] pt-3 text-xs font-semibold text-[#7d4308]">启用该类目前，必须选择完整的类目专属提示词。</p>}
                  </div>
                )
              })}
            </div>
          </div>
        </section>

        <section className="mt-8 grid gap-7 border-y border-[var(--line-strong)] bg-white px-5 py-6 lg:grid-cols-[230px_1fr] lg:px-7">
          <div><h2 className="font-editorial text-2xl font-bold">调用参数</h2><p className="mt-2 text-sm leading-6 text-[var(--muted)]">V2.1 默认使用低随机性。并发数只控制 Worker，单次图片仍按 A、B 两次顺序调用。</p></div>
          {form && (
            <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
              <Field label="Temperature"><Input type="number" step="0.1" min="0" max="2" value={form.temperature} onChange={(event) => update("temperature", Number(event.target.value))} /></Field>
              <Field label="最大输出 Token"><Input type="number" min="128" value={form.max_tokens} onChange={(event) => update("max_tokens", Number(event.target.value))} /></Field>
              <Field label="超时（秒）"><Input type="number" min="10" value={form.timeout_seconds} onChange={(event) => update("timeout_seconds", Number(event.target.value))} /></Field>
              <Field label="失败重试"><Input type="number" min="0" max="5" value={form.max_retries} onChange={(event) => update("max_retries", Number(event.target.value))} /></Field>
              <Field label="最大并发"><Input type="number" min="1" max="10" value={form.max_concurrency} onChange={(event) => update("max_concurrency", Number(event.target.value))} /></Field>
              <Field label="单次输入上限 Token"><Input type="number" min="0" value={form.max_input_tokens} onChange={(event) => update("max_input_tokens", Number(event.target.value))} /></Field>
              <Field label="输入计价 / 百万 Token（micros）"><Input type="number" min="0" value={form.input_micros_per_million_tokens} onChange={(event) => update("input_micros_per_million_tokens", Number(event.target.value))} /></Field>
              <Field label="输出计价 / 百万 Token（micros）"><Input type="number" min="0" value={form.output_micros_per_million_tokens} onChange={(event) => update("output_micros_per_million_tokens", Number(event.target.value))} /></Field>
              <label className="flex min-h-20 items-center justify-between gap-4 border border-[var(--line)] bg-[#fafbf8] px-4"><span><span className="block text-sm font-semibold">结构化输出</span><span className="mt-1 block text-xs text-[var(--muted)]">仍保留服务端 JSON 校验</span></span><input type="checkbox" checked={form.structured_output} onChange={(event) => update("structured_output", event.target.checked)} className="size-5 accent-[#11130f]" /></label>
              <label className="flex min-h-20 items-center justify-between gap-4 border border-[var(--line)] bg-[#fafbf8] px-4 sm:col-span-2 xl:col-span-3"><span><span className="block text-sm font-semibold">高风险结果自动复核</span><span className="mt-1 block text-xs leading-5 text-[var(--muted)]">仅在专业摄影、L4/L5或出现5级维度时增加一次短调用；复核只能保持或降级，不会抬高分数。</span></span><input type="checkbox" checked={form.high_risk_review_enabled} onChange={(event) => update("high_risk_review_enabled", event.target.checked)} className="size-5 shrink-0 accent-[#11130f]" /></label>
              <label className="flex min-h-20 items-center justify-between gap-4 border border-[#e8c876] bg-[#fff9e9] px-4 sm:col-span-2 xl:col-span-3"><span><span className="block text-sm font-semibold">允许参与真实横评</span><span className="mt-1 block text-xs leading-5 text-[#6f5513]">必须同时配置密钥、输入上限和非零计价；此开关不等于自动运行。</span></span><input type="checkbox" checked={form.benchmark_enabled} onChange={(event) => { update("benchmark_enabled", event.target.checked); setMainBenchmarkConfirmed(false) }} className="size-5 shrink-0" /></label>
              {form.benchmark_enabled && <label className="flex items-start gap-3 border-y border-[#c55b52] bg-[#fff0ee] px-4 py-3 text-xs font-semibold leading-5 text-[#7d201a] sm:col-span-2 xl:col-span-3"><input className="mt-1 size-4" type="checkbox" checked={mainBenchmarkConfirmed} onChange={(event) => setMainBenchmarkConfirmed(event.target.checked)} /><span>确认该模型的端点、输入上限和计价已由管理员核对；真实横评仍需单独冻结预算。</span></label>}
            </div>
          )}
        </section>

        <section className="mt-10 border-y border-[var(--line-strong)] bg-white">
          <div className="grid gap-7 border-b border-[var(--line)] px-5 py-6 lg:grid-cols-[230px_1fr] lg:px-7">
            <div>
              <div className="flex size-10 items-center justify-center rounded-[4px] bg-primary"><Key size={21} weight="bold" /></div>
              <h2 className="font-editorial mt-5 text-2xl font-bold">提示词诊断模型</h2>
              <p className="mt-2 text-sm leading-6 text-[var(--muted)]">读取人工纠错样本，定位高频误判并生成候选提示词。可使用任意 OpenAI-compatible 高能力模型，不会直接覆盖正式提示词。</p>
            </div>
            {optimizerForm ? <div className="grid gap-5 md:grid-cols-2">
              <Field label="配置名称"><Input value={optimizerForm.name} onChange={(event) => updateOptimizer("name", event.target.value)} /></Field>
              <Field label="渠道标识"><Input value={optimizerForm.provider} onChange={(event) => updateOptimizer("provider", event.target.value)} placeholder="openai / deepseek / custom" /></Field>
              <Field label="模型 ID"><Input value={optimizerForm.model_id} onChange={(event) => updateOptimizer("model_id", event.target.value)} /></Field>
              <Field label="Base URL"><Input value={optimizerForm.base_url} onChange={(event) => updateOptimizer("base_url", event.target.value)} /></Field>
              <Field label="API 路径"><Input value={optimizerForm.api_path} onChange={(event) => updateOptimizer("api_path", event.target.value)} /></Field>
              <div className="md:col-span-2"><Field label="API Key"><Input type="password" value={optimizerForm.api_key} onChange={(event) => updateOptimizer("api_key", event.target.value)} placeholder={optimizerConfig.data?.has_api_key ? "留空以保留当前密钥" : "请输入当前渠道 API Key"} autoComplete="new-password" /></Field></div>
              <Field label="最大输出 Token"><Input type="number" min="512" value={optimizerForm.max_tokens} onChange={(event) => updateOptimizer("max_tokens", Number(event.target.value))} /></Field>
              <Field label="超时（秒）"><Input type="number" min="10" value={optimizerForm.timeout_seconds} onChange={(event) => updateOptimizer("timeout_seconds", Number(event.target.value))} /></Field>
              <Field label="单次输入上限 Token"><Input type="number" min="0" value={optimizerForm.max_input_tokens} onChange={(event) => updateOptimizer("max_input_tokens", Number(event.target.value))} /></Field>
              <Field label="输入计价 / 百万 Token（micros）"><Input type="number" min="0" value={optimizerForm.input_micros_per_million_tokens} onChange={(event) => updateOptimizer("input_micros_per_million_tokens", Number(event.target.value))} /></Field>
              <Field label="输出计价 / 百万 Token（micros）"><Input type="number" min="0" value={optimizerForm.output_micros_per_million_tokens} onChange={(event) => updateOptimizer("output_micros_per_million_tokens", Number(event.target.value))} /></Field>
            </div> : <div className="h-44 animate-pulse bg-[#f1f3ef]" />}
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4 lg:px-7">
            <div className="flex items-center gap-2">{optimizerConfig.data?.has_api_key ? <CheckCircle size={20} weight="fill" className="text-[#2f6f48]" /> : <WarningCircle size={20} className="text-[#a85a0a]" />}<span className="text-sm font-semibold">{optimizerConfig.data?.has_api_key ? "当前电脑已保存诊断模型密钥" : "尚未保存诊断模型密钥"}</span></div>
            <div className="flex gap-2"><Button variant="secondary" onClick={() => testOptimizer.mutate()} disabled={!optimizerConfig.data?.has_api_key || testOptimizer.isPending}><PlugsConnected />测试诊断模型连接</Button><Button onClick={() => saveOptimizer.mutate()} disabled={!optimizerForm || saveOptimizer.isPending}><FloppyDisk />保存诊断模型配置</Button></div>
          </div>
        </section>

        <section className="mt-10 border-y border-[var(--line-strong)] bg-white">
          <div className="grid gap-7 border-b border-[var(--line)] px-5 py-6 lg:grid-cols-[230px_1fr] lg:px-7">
            <div><div className="flex size-10 items-center justify-center rounded-[4px] border border-[var(--line-strong)]"><Plus size={21} /></div><h2 className="font-editorial mt-5 text-2xl font-bold">新增横评模型</h2><p className="mt-2 text-sm leading-6 text-[var(--muted)]">每个配置使用独立系统凭据引用。默认不启用，只有显式确认后才进入真实横评可选项。</p></div>
            <div className="grid gap-5 md:grid-cols-2">
              <Field label="配置名称"><Input value={benchmarkForm.name} onChange={(event) => updateBenchmark("name", event.target.value)} /></Field>
              <Field label="模型 ID"><Input value={benchmarkForm.model_id} onChange={(event) => updateBenchmark("model_id", event.target.value)} /></Field>
              <Field label="渠道标识"><Input value={benchmarkForm.provider} onChange={(event) => updateBenchmark("provider", event.target.value)} placeholder="openai / deepseek / claude-compatible" /></Field>
              <Field label="API 路径"><Input value={benchmarkForm.api_path} onChange={(event) => updateBenchmark("api_path", event.target.value)} /></Field>
              <div className="md:col-span-2"><Field label="Base URL"><Input value={benchmarkForm.base_url} onChange={(event) => updateBenchmark("base_url", event.target.value)} /></Field></div>
              <div className="md:col-span-2"><Field label="API Key"><Input type="password" value={benchmarkForm.api_key} onChange={(event) => updateBenchmark("api_key", event.target.value)} autoComplete="new-password" /></Field></div>
              <Field label="最大输入 Token"><Input type="number" min="1" value={benchmarkForm.max_input_tokens} onChange={(event) => updateBenchmark("max_input_tokens", Number(event.target.value))} /></Field>
              <Field label="最大输出 Token"><Input type="number" min="128" value={benchmarkForm.max_tokens} onChange={(event) => updateBenchmark("max_tokens", Number(event.target.value))} /></Field>
              <Field label="输入计价 / 百万 Token"><Input type="number" min="1" value={benchmarkForm.input_micros_per_million_tokens} onChange={(event) => updateBenchmark("input_micros_per_million_tokens", Number(event.target.value))} /></Field>
              <Field label="输出计价 / 百万 Token"><Input type="number" min="1" value={benchmarkForm.output_micros_per_million_tokens} onChange={(event) => updateBenchmark("output_micros_per_million_tokens", Number(event.target.value))} /></Field>
              <label className="flex items-center justify-between gap-4 border-y border-[#e8c876] bg-[#fff9e9] px-4 py-3 text-sm font-semibold md:col-span-2"><span>允许参与真实横评</span><input type="checkbox" checked={benchmarkForm.benchmark_enabled} onChange={(event) => { updateBenchmark("benchmark_enabled", event.target.checked); setBenchmarkCreateConfirmed(false) }} /></label>
              {benchmarkForm.benchmark_enabled && <label className="flex items-start gap-3 border-y border-[#c55b52] bg-[#fff0ee] px-4 py-3 text-xs font-semibold leading-5 text-[#7d201a] md:col-span-2"><input className="mt-1 size-4" type="checkbox" checked={benchmarkCreateConfirmed} onChange={(event) => setBenchmarkCreateConfirmed(event.target.checked)} /><span>确认端点、模型、输入上限和计价均已核对。创建后仍不会自动执行。</span></label>}
            </div>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4 lg:px-7"><p className="text-xs text-[var(--muted)]">已配置 {(modelConfigs.data?.items ?? []).filter((item) => !item.active).length} 个独立横评模型。</p><Button onClick={() => createBenchmarkConfig.mutate()} disabled={createBenchmarkConfig.isPending || !benchmarkForm.name || !benchmarkForm.base_url || !benchmarkForm.model_id || !benchmarkForm.api_key || !benchmarkForm.benchmark_enabled || !benchmarkCreateConfirmed || benchmarkForm.max_input_tokens <= 0 || benchmarkForm.input_micros_per_million_tokens <= 0 || benchmarkForm.output_micros_per_million_tokens <= 0}><Plus />创建横评配置</Button></div>
          <div className="divide-y divide-[var(--line)] border-t border-[var(--line)]">{(modelConfigs.data?.items ?? []).filter((item) => !item.active).map((item) => <div key={item.id} className="grid gap-2 px-5 py-4 text-sm md:grid-cols-[1fr_auto_auto_auto]"><div><strong>{item.name}</strong><p className="font-data mt-1 text-xs text-[var(--muted)]">{item.model_id}</p></div><Badge>{item.has_api_key ? "密钥已配置" : "未配置密钥"}</Badge><Badge>{item.max_input_tokens} input</Badge><Badge tone={item.benchmark_enabled ? "warning" : "neutral"}>{item.benchmark_enabled ? "横评已启用" : "横评关闭"}</Badge></div>)}</div>
        </section>

        <section className="mt-10 grid gap-7 border-y border-[var(--line-strong)] bg-white px-5 py-6 lg:grid-cols-[230px_1fr] lg:px-7">
          <div>
            <div className="flex size-10 items-center justify-center rounded-[4px] border border-[var(--line-strong)]"><SlidersHorizontal size={21} /></div>
            <h2 className="font-editorial mt-5 text-2xl font-bold">智能抽样策略</h2>
            <p className="mt-2 text-sm leading-6 text-[var(--muted)]">调整结果进入必审或抽样队列的阈值。每次保存生成新修订号，列表与详情同步显示当前策略版本。</p>
            {samplingForm && <Badge>{samplingForm.version}</Badge>}
          </div>
          {samplingForm ? (
            <div>
              <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
                <Field label="常规稳定抽样（%）"><Input type="number" min="0" max="100" value={samplingForm.sample_rate} onChange={(event) => updateSampling("sample_rate", Number(event.target.value))} /></Field>
                <Field label="低置信度必审阈值（%）"><Input type="number" min="0" max="100" value={Math.round(samplingForm.low_confidence_threshold * 100)} onChange={(event) => updateSampling("low_confidence_threshold", Number(event.target.value) / 100)} /></Field>
                <Field label="中置信度抽样上限（%）"><Input type="number" min="0" max="100" value={Math.round(samplingForm.medium_confidence_threshold * 100)} onChange={(event) => updateSampling("medium_confidence_threshold", Number(event.target.value) / 100)} /></Field>
                <Field label="新组合冷启动必审数"><Input type="number" min="0" max="100" value={samplingForm.cold_start_required_count} onChange={(event) => updateSampling("cold_start_required_count", Number(event.target.value))} /></Field>
                <Field label="高等级从哪一级开始必审"><select className="flex h-11 w-full rounded-[4px] border border-input bg-transparent px-3 text-sm" value={samplingForm.high_level_required_from} onChange={(event) => updateSampling("high_level_required_from", Number(event.target.value))}>{[1, 2, 3, 4, 5].map((level) => <option key={level} value={level}>L{level} 及以上</option>)}</select></Field>
              </div>
              <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-[var(--line)] pt-4">
                <p className="text-xs text-[var(--muted)]">最后更新：{samplingForm.updated_by} · {new Date(samplingForm.updated_at).toLocaleString("zh-CN")}</p>
                <Button onClick={() => saveSampling.mutate()} disabled={saveSampling.isPending || samplingForm.medium_confidence_threshold < samplingForm.low_confidence_threshold}><FloppyDisk />保存抽样策略</Button>
              </div>
              {samplingForm.medium_confidence_threshold < samplingForm.low_confidence_threshold && <p role="alert" className="mt-3 text-sm font-semibold text-[var(--danger)]">中置信度抽样上限不能低于低置信度必审阈值。</p>}
            </div>
          ) : <div className="h-44 animate-pulse bg-[#f1f3ef]" />}
        </section>

        <section className="mt-10 grid gap-7 border-y border-[var(--line-strong)] bg-white px-5 py-6 lg:grid-cols-[230px_1fr] lg:px-7">
          <div>
            <div className="flex size-10 items-center justify-center rounded-[4px] border border-[var(--line-strong)]"><SlidersHorizontal size={21} /></div>
            <h2 className="font-editorial mt-5 text-2xl font-bold">初审工作流</h2>
            <p className="mt-2 text-sm leading-6 text-[var(--muted)]">初期可由 1 位人员完成全流水线；团队扩容后切换为 3、5、7 或 9 人盲审多数共识。人数在初审组创建时冻结，修改配置不会改变已开始的任务。</p>
            {reviewWorkflowForm && <Badge>{reviewWorkflowForm.version}</Badge>}
          </div>
          {reviewWorkflowForm ? (
            <div>
              <div className="grid gap-5 sm:grid-cols-2">
                <Field label="每个初审组审核员人数">
                  <select
                    className="flex h-11 w-full rounded-[4px] border border-input bg-transparent px-3 text-sm"
                    value={reviewWorkflowForm.initial_reviewers}
                    onChange={(event) => updateReviewWorkflow(Number(event.target.value) as 1 | 3 | 5 | 7 | 9)}
                  >
                    {reviewWorkflowForm.supported_reviewer_counts.map((count) => (
                      <option key={count} value={count}>
                        {count === 1 ? "1 人（单人即时定案）" : `${count} 人（严格多数共识）`}
                      </option>
                    ))}
                  </select>
                </Field>
                <div className="rounded-[4px] border border-[var(--line)] bg-[#f7f8f5] px-4 py-3 text-sm leading-6 text-[var(--muted)]">
                  {reviewWorkflowForm.initial_reviewers === 1
                    ? "首位审核员提交后即形成最终人工真值，并进入提示词优化队列。"
                    : "审核答案在组内完成前保持盲审；逐字段无严格多数时进入初审主审裁决。"}
                </div>
              </div>
              <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-[var(--line)] pt-4">
                <p className="text-xs text-[var(--muted)]">最后更新：{reviewWorkflowForm.updated_by} · {new Date(reviewWorkflowForm.updated_at).toLocaleString("zh-CN")}</p>
                <Button onClick={() => saveReviewWorkflow.mutate()} disabled={saveReviewWorkflow.isPending}><FloppyDisk />保存初审工作流</Button>
              </div>
            </div>
          ) : <div className="h-36 animate-pulse bg-[#f1f3ef]" />}
        </section>

        <div className="mt-8 flex justify-end"><Button onClick={() => save.mutate()} disabled={!form || save.isPending || (form.benchmark_enabled && !mainBenchmarkConfirmed)}><FloppyDisk />保存主模型配置</Button></div>
      </div>
    </>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="block"><span className="mb-2 block text-xs font-semibold">{label}</span>{children}</label>
}
