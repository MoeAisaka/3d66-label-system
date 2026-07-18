import { useEffect, useState, type ReactNode } from "react"
import { CheckCircle, FloppyDisk, Key, PlugsConnected, WarningCircle } from "@phosphor-icons/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api, jsonBody } from "@/lib/api"
import type { ModelConfig, OptimizerConfig } from "@/lib/types"

type FormState = Omit<ModelConfig, "id" | "provider" | "api_key_mask" | "updated_at" | "has_api_key"> & { api_key: string }
type OptimizerFormState = Omit<OptimizerConfig, "id" | "provider" | "api_key_mask" | "updated_at" | "has_api_key"> & { api_key: string }

export function ModelPage() {
  const queryClient = useQueryClient()
  const config = useQuery({ queryKey: ["model-config"], queryFn: () => api<ModelConfig>("/api/model-config") })
  const optimizerConfig = useQuery({ queryKey: ["optimizer-config"], queryFn: () => api<OptimizerConfig>("/api/optimizer-config") })
  const [form, setForm] = useState<FormState | null>(null)
  const [optimizerForm, setOptimizerForm] = useState<OptimizerFormState | null>(null)
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

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((current) => current ? { ...current, [key]: value } : current)
  }

  function updateOptimizer<K extends keyof OptimizerFormState>(key: K, value: OptimizerFormState[K]) {
    setOptimizerForm((current) => current ? { ...current, [key]: value } : current)
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

        <div className="mt-8 flex justify-end"><Button onClick={() => save.mutate()} disabled={!form || save.isPending}><FloppyDisk />保存豆包配置</Button></div>
      </div>
    </>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="block"><span className="mb-2 block text-xs font-semibold">{label}</span>{children}</label>
}
