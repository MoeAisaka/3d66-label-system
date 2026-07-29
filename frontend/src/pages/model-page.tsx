import { useEffect, useState, type ReactNode } from "react"
import { CheckCircle, FloppyDisk, Key, PlugsConnected, SlidersHorizontal, WarningCircle } from "@phosphor-icons/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api, jsonBody } from "@/lib/api"
import type { ModelConfig, OptimizerConfig, ReviewWorkflowPolicy, SamplingPolicy } from "@/lib/types"

type FormState = Omit<ModelConfig, "id" | "provider" | "api_key_mask" | "updated_at" | "has_api_key"> & { api_key: string }
type OptimizerFormState = Omit<OptimizerConfig, "id" | "provider" | "api_key_mask" | "updated_at" | "has_api_key"> & { api_key: string }

export function ModelPage() {
  const queryClient = useQueryClient()
  const config = useQuery({ queryKey: ["model-config"], queryFn: () => api<ModelConfig>("/api/model-config") })
  const optimizerConfig = useQuery({ queryKey: ["optimizer-config"], queryFn: () => api<OptimizerConfig>("/api/optimizer-config") })
  const samplingPolicy = useQuery({ queryKey: ["sampling-policy"], queryFn: () => api<SamplingPolicy>("/api/sampling-policy") })
  const reviewWorkflowPolicy = useQuery({ queryKey: ["review-workflow-policy"], queryFn: () => api<ReviewWorkflowPolicy>("/api/review-workflow-policy") })
  const [form, setForm] = useState<FormState | null>(null)
  const [optimizerForm, setOptimizerForm] = useState<OptimizerFormState | null>(null)
  const [samplingForm, setSamplingForm] = useState<SamplingPolicy | null>(null)
  const [reviewWorkflowForm, setReviewWorkflowForm] = useState<ReviewWorkflowPolicy | null>(null)
  useEffect(() => {
    if (!config.data) return
    const { id: _id, provider: _provider, api_key_mask: _mask, updated_at: _updated, has_api_key: _hasApiKey, ...rest } = config.data
    setForm({ ...rest, api_key: "" })
  }, [config.data])
  useEffect(() => {
    if (!optimizerConfig.data) return
    const { id: _id, provider: _provider, api_key_mask: _mask, updated_at: _updated, has_api_key: _hasApiKey, ...rest } = optimizerConfig.data
    setOptimizerForm({ ...rest, api_key: "" })
  }, [optimizerConfig.data])
  useEffect(() => {
    if (samplingPolicy.data) setSamplingForm(samplingPolicy.data)
  }, [samplingPolicy.data])
  useEffect(() => {
    if (reviewWorkflowPolicy.data) setReviewWorkflowForm(reviewWorkflowPolicy.data)
  }, [reviewWorkflowPolicy.data])

  const save = useMutation({
    mutationFn: () => api("/api/model-config", { method: "PUT", ...jsonBody(form) }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["model-config"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
      ])
      setForm((current) => current ? { ...current, api_key: "" } : current)
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
      toast.success("SOL 提示词诊断模型配置已保存")
    },
    onError: (error) => toast.error(error.message),
  })
  const testOptimizer = useMutation({
    mutationFn: () => api<{ ok: boolean; message: string }>("/api/optimizer-config/test", { method: "POST" }),
    onSuccess: (data) => toast.success(data.message || "SOL 连接成功"),
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

  return (
    <>
      <PageHeader
        index="06"
        title="模型配置"
        description="豆包负责批量评测，SOL 负责根据人工校验样本诊断提示词。两套 API Key 分开保存，只写入当前 Windows 电脑。"
        actions={
          <>
            <Button variant="secondary" onClick={() => test.mutate()} disabled={!config.data?.has_api_key || test.isPending}><PlugsConnected />测试豆包连接</Button>
            <Button onClick={() => save.mutate()} disabled={!form || save.isPending}><FloppyDisk />保存豆包配置</Button>
          </>
        }
      />
      <div className="mx-auto max-w-[1180px] px-5 py-7 md:px-8 lg:px-10 lg:py-10">
        <section className="grid gap-7 border-y border-[var(--line-strong)] bg-white px-5 py-6 lg:grid-cols-[230px_1fr] lg:px-7">
          <div>
            <div className="flex size-10 items-center justify-center rounded-[4px] bg-primary"><PlugsConnected size={21} weight="bold" /></div>
            <h2 className="font-editorial mt-5 text-2xl font-bold">连接信息</h2>
            <p className="mt-2 text-sm leading-6 text-[var(--muted)]">支持火山方舟 OpenAI 兼容的 Chat Completions 接口。模型 ID 也可以填写推理接入点 ID。</p>
          </div>
          {form ? (
            <div className="grid gap-5 md:grid-cols-2">
              <Field label="配置名称"><Input value={form.name} onChange={(event) => update("name", event.target.value)} /></Field>
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
            <Field label="输入新的 API Key"><Input type="password" value={form?.api_key ?? ""} onChange={(event) => update("api_key", event.target.value)} placeholder={config.data?.has_api_key ? "留空以保留当前密钥" : "请输入火山方舟 API Key"} autoComplete="new-password" /></Field>
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
              <label className="flex min-h-20 items-center justify-between gap-4 border border-[var(--line)] bg-[#fafbf8] px-4"><span><span className="block text-sm font-semibold">结构化输出</span><span className="mt-1 block text-xs text-[var(--muted)]">仍保留服务端 JSON 校验</span></span><input type="checkbox" checked={form.structured_output} onChange={(event) => update("structured_output", event.target.checked)} className="size-5 accent-[#11130f]" /></label>
              <label className="flex min-h-20 items-center justify-between gap-4 border border-[var(--line)] bg-[#fafbf8] px-4 sm:col-span-2 xl:col-span-3"><span><span className="block text-sm font-semibold">高风险结果自动复核</span><span className="mt-1 block text-xs leading-5 text-[var(--muted)]">仅在专业摄影、L4/L5或出现5级维度时增加一次短调用；复核只能保持或降级，不会抬高分数。</span></span><input type="checkbox" checked={form.high_risk_review_enabled} onChange={(event) => update("high_risk_review_enabled", event.target.checked)} className="size-5 shrink-0 accent-[#11130f]" /></label>
            </div>
          )}
        </section>

        <section className="mt-10 border-y border-[var(--line-strong)] bg-white">
          <div className="grid gap-7 border-b border-[var(--line)] px-5 py-6 lg:grid-cols-[230px_1fr] lg:px-7">
            <div>
              <div className="flex size-10 items-center justify-center rounded-[4px] bg-primary"><Key size={21} weight="bold" /></div>
              <h2 className="font-editorial mt-5 text-2xl font-bold">SOL 提示词诊断模型</h2>
              <p className="mt-2 text-sm leading-6 text-[var(--muted)]">读取人工纠错样本，定位高频误判并生成候选提示词。不会直接覆盖豆包正式提示词。</p>
            </div>
            {optimizerForm ? <div className="grid gap-5 md:grid-cols-2">
              <Field label="配置名称"><Input value={optimizerForm.name} onChange={(event) => updateOptimizer("name", event.target.value)} /></Field>
              <Field label="模型 ID"><Input value={optimizerForm.model_id} onChange={(event) => updateOptimizer("model_id", event.target.value)} /></Field>
              <Field label="Base URL"><Input value={optimizerForm.base_url} onChange={(event) => updateOptimizer("base_url", event.target.value)} /></Field>
              <Field label="API 路径"><Input value={optimizerForm.api_path} onChange={(event) => updateOptimizer("api_path", event.target.value)} /></Field>
              <div className="md:col-span-2"><Field label="OpenAI API Key"><Input type="password" value={optimizerForm.api_key} onChange={(event) => updateOptimizer("api_key", event.target.value)} placeholder={optimizerConfig.data?.has_api_key ? "留空以保留当前密钥" : "请输入可调用 SOL 的 API Key"} autoComplete="new-password" /></Field></div>
              <Field label="最大输出 Token"><Input type="number" min="512" value={optimizerForm.max_tokens} onChange={(event) => updateOptimizer("max_tokens", Number(event.target.value))} /></Field>
              <Field label="超时（秒）"><Input type="number" min="10" value={optimizerForm.timeout_seconds} onChange={(event) => updateOptimizer("timeout_seconds", Number(event.target.value))} /></Field>
            </div> : <div className="h-44 animate-pulse bg-[#f1f3ef]" />}
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4 lg:px-7">
            <div className="flex items-center gap-2">{optimizerConfig.data?.has_api_key ? <CheckCircle size={20} weight="fill" className="text-[#2f6f48]" /> : <WarningCircle size={20} className="text-[#a85a0a]" />}<span className="text-sm font-semibold">{optimizerConfig.data?.has_api_key ? "当前电脑已保存 SOL 密钥" : "尚未保存 SOL 密钥"}</span></div>
            <div className="flex gap-2"><Button variant="secondary" onClick={() => testOptimizer.mutate()} disabled={!optimizerConfig.data?.has_api_key || testOptimizer.isPending}><PlugsConnected />测试 SOL 连接</Button><Button onClick={() => saveOptimizer.mutate()} disabled={!optimizerForm || saveOptimizer.isPending}><FloppyDisk />保存 SOL 配置</Button></div>
          </div>
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

        <div className="mt-8 flex justify-end"><Button onClick={() => save.mutate()} disabled={!form || save.isPending}><FloppyDisk />保存豆包配置</Button></div>
      </div>
    </>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="block"><span className="mb-2 block text-xs font-semibold">{label}</span>{children}</label>
}
