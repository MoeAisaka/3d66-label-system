import { useMemo, useState, type FormEvent } from "react"
import { CheckCircle, FloppyDisk, PencilSimple, PlugsConnected, Plus, Power, X } from "@phosphor-icons/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api, jsonBody } from "@/lib/api"
import type { ModelRegistryEntry } from "@/lib/types"

type RegistryRole = ModelRegistryEntry["role"]
type RegistryDraft = Omit<ModelRegistryEntry, "id" | "source_model_config_id" | "source_optimizer_config_id" | "has_api_key" | "api_key_mask" | "created_by" | "created_at" | "updated_at"> & { api_key: string }

const ROLE_LABELS: Record<RegistryRole, string> = { main: "主模型", tuning: "调优模型", benchmark: "横评候选" }
const PROTOCOL_LABELS: Record<ModelRegistryEntry["protocol"], string> = {
  openai_chat: "OpenAI Chat Completions",
  openai_responses: "OpenAI Responses",
  anthropic_messages: "Anthropic Messages",
  custom_json: "受控 OpenAI-compatible JSON",
}

const emptyDraft = (): RegistryDraft => ({
  role: "main",
  name: "",
  provider: "doubao",
  protocol: "openai_chat",
  capabilities: ["text", "vision", "structured_output"],
  description: "",
  base_url: "https://ark.cn-beijing.volces.com/api/v3",
  api_path: "/chat/completions",
  model_id: "",
  api_key: "",
  temperature: 0.1,
  max_tokens: 4096,
  timeout_seconds: 120,
  max_retries: 1,
  max_concurrency: 8,
  max_requests_per_minute: 0,
  max_input_tokens: 0,
  input_micros_per_million_tokens: 0,
  output_micros_per_million_tokens: 0,
  monthly_budget_micros: 0,
  thinking_mode: "auto",
  level: "standard",
  structured_output: true,
  active: true,
})

export function ModelRegistryPage() {
  const queryClient = useQueryClient()
  const registry = useQuery({ queryKey: ["model-registry"], queryFn: () => api<{ items: ModelRegistryEntry[] }>("/api/model-registry") })
  const [role, setRole] = useState<RegistryRole | "all">("all")
  const [draft, setDraft] = useState<RegistryDraft | null>(null)
  const [editingId, setEditingId] = useState<number | null>(null)
  const items = useMemo(() => (registry.data?.items ?? []).filter((item) => role === "all" || item.role === role), [registry.data?.items, role])

  const save = useMutation({
    mutationFn: async () => {
      if (!draft) throw new Error("请先填写模型配置")
      const path = editingId ? `/api/model-registry/${editingId}` : "/api/model-registry"
      return api<ModelRegistryEntry>(path, { method: editingId ? "PUT" : "POST", ...jsonBody(draft) })
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["model-registry"] })
      setDraft(null)
      setEditingId(null)
      toast.success("模型注册项已保存")
    },
    onError: (error) => toast.error(error.message),
  })
  const toggle = useMutation({
    mutationFn: ({ id, active }: { id: number; active: boolean }) => api<ModelRegistryEntry>(`/api/model-registry/${id}/${active ? "deactivate" : "activate"}`, { method: "POST" }),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["model-registry"] }); toast.success("模型状态已更新") },
    onError: (error) => toast.error(error.message),
  })
  const test = useMutation({
    mutationFn: (id: number) => api<{ ok: boolean; message: string }>(`/api/model-registry/${id}/test`, { method: "POST" }),
    onSuccess: (data) => toast.success(data.message),
    onError: (error) => toast.error(error.message),
  })

  function openCreate() { setEditingId(null); setDraft(emptyDraft()) }
  function openEdit(item: ModelRegistryEntry) {
    const { id: _id, source_model_config_id: _model, source_optimizer_config_id: _optimizer, has_api_key: _has, api_key_mask: _mask, created_by: _createdBy, created_at: _createdAt, updated_at: _updatedAt, ...rest } = item
    setEditingId(item.id)
    setDraft({ ...rest, api_key: "" })
  }
  function update<K extends keyof RegistryDraft>(key: K, value: RegistryDraft[K]) { setDraft((current) => current ? { ...current, [key]: value } : current) }
  function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); save.mutate() }

  return (
    <>
      <PageHeader
        index="06"
        title="模型注册中心"
        description="以列表维护主模型、调优模型和横评候选。协议、Token、价格、调用限制、思考开关与层级均可独立配置；任务只冻结非密快照。"
        actions={<Button onClick={openCreate}><Plus />新建模型</Button>}
      />
      <main className="mx-auto max-w-[1540px] px-5 py-8 md:px-8 lg:px-10">
        <section className="border-y border-[var(--line-strong)] bg-white">
          <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[var(--line)] px-5 py-4">
            <div>
              <h2 className="font-editorial text-2xl font-bold">已注册模型</h2>
              <p className="mt-1 text-sm text-[var(--muted)]">密钥只展示是否已配置，不返回明文或安全存储引用。</p>
            </div>
            <div className="flex flex-wrap gap-1 rounded-[4px] border border-[var(--line-strong)] p-1">
              {(["all", "main", "tuning", "benchmark"] as const).map((value) => (
                <button key={value} type="button" onClick={() => setRole(value)} className={`rounded-[3px] px-3 py-2 text-xs font-semibold ${role === value ? "bg-[#11130f] text-white" : "text-[var(--muted)] hover:bg-[#f1f3ef]"}`}>
                  {value === "all" ? "全部" : ROLE_LABELS[value]}
                </button>
              ))}
            </div>
          </div>
          <div className="overflow-x-auto">
            <div className="min-w-[1040px] divide-y divide-[var(--line)]">
              <div className="grid grid-cols-[1.45fr_0.85fr_1.4fr_1.15fr_1fr_0.85fr_180px] gap-4 bg-[#f7f8f5] px-5 py-3 text-[0.68rem] font-bold uppercase tracking-[0.12em] text-[var(--muted)]">
                <span>模型</span><span>角色</span><span>协议 / 能力</span><span>调用限制</span><span>计价 / 层级</span><span>状态</span><span>操作</span>
              </div>
              {items.map((item) => (
                <div key={item.id} className="grid grid-cols-[1.45fr_0.85fr_1.4fr_1.15fr_1fr_0.85fr_180px] items-center gap-4 px-5 py-4 text-sm">
                  <div className="min-w-0"><p className="truncate font-semibold">{item.name}</p><p className="file-name mt-1 truncate text-xs">{item.provider} · {item.model_id}</p></div>
                  <Badge tone={item.role === "tuning" ? "warning" : item.role === "main" ? "success" : "neutral"}>{ROLE_LABELS[item.role]}</Badge>
                  <div className="min-w-0"><p className="truncate text-xs font-semibold">{PROTOCOL_LABELS[item.protocol]}</p><p className="mt-1 truncate text-xs text-[var(--muted)]">{item.capabilities.join(" · ") || "未声明能力"}</p></div>
                  <div className="text-xs leading-5 text-[var(--muted)]"><p>并发 {item.max_concurrency} · 超时 {item.timeout_seconds}s</p><p>输入 {item.max_input_tokens || "不限"} · 速率 {item.max_requests_per_minute || "不限"}</p></div>
                  <div className="text-xs leading-5 text-[var(--muted)]"><p>入 {item.input_micros_per_million_tokens} / 出 {item.output_micros_per_million_tokens}</p><p>层级 {item.level} · {item.thinking_mode === "enabled" ? "思考开" : item.thinking_mode === "disabled" ? "思考关" : "思考自动"}</p></div>
                  <div className="space-y-1"><Badge tone={item.active ? "success" : "neutral"}>{item.active ? "启用" : "停用"}</Badge><p className="text-xs text-[var(--muted)]">{item.has_api_key ? "密钥已配置" : "缺少密钥"}</p></div>
                  <div className="flex flex-wrap gap-1"><Button size="sm" variant="secondary" onClick={() => openEdit(item)}><PencilSimple />编辑</Button><Button size="sm" variant="secondary" onClick={() => test.mutate(item.id)} disabled={!item.has_api_key || test.isPending}><PlugsConnected />测试</Button><Button size="sm" variant={item.active ? "danger" : "secondary"} onClick={() => toggle.mutate({ id: item.id, active: item.active })}><Power />{item.active ? "停用" : "启用"}</Button></div>
                </div>
              ))}
              {!items.length && <div className="px-5 py-12 text-center text-sm text-[var(--muted)]">暂无符合条件的模型注册项。</div>}
            </div>
          </div>
        </section>
      </main>
      {draft && (
        <div className="fixed inset-0 z-40 bg-black/20" onMouseDown={(event) => { if (event.target === event.currentTarget) { setDraft(null); setEditingId(null) } }}>
          <aside role="dialog" aria-modal="true" aria-label={editingId ? "编辑模型" : "新建模型"} className="absolute inset-y-0 right-0 flex w-full max-w-[620px] flex-col overflow-y-auto border-l border-[var(--line-strong)] bg-white shadow-2xl">
            <div className="flex items-center justify-between border-b border-[var(--line)] px-5 py-4"><div><h2 className="font-editorial text-2xl font-bold">{editingId ? "编辑模型" : "新建模型"}</h2><p className="mt-1 text-xs text-[var(--muted)]">API Key 仅用于安全存储，留空表示保留已有凭据。</p></div><Button size="icon" variant="ghost" aria-label="关闭" onClick={() => { setDraft(null); setEditingId(null) }}><X /></Button></div>
            <form className="space-y-6 px-5 py-6" onSubmit={submit}>
              <div className="grid gap-4 sm:grid-cols-2"><Field label="模型角色"><select className="h-11 w-full rounded-[4px] border border-input bg-white px-3 text-sm" value={draft.role} disabled={editingId !== null} onChange={(event) => update("role", event.target.value as RegistryRole)}><option value="main">主模型</option><option value="tuning">调优模型</option><option value="benchmark">横评候选</option></select></Field><Field label="配置名称"><Input required value={draft.name} onChange={(event) => update("name", event.target.value)} /></Field><Field label="供应商"><Input required value={draft.provider} onChange={(event) => update("provider", event.target.value)} /></Field><Field label="模型 ID"><Input required value={draft.model_id} onChange={(event) => update("model_id", event.target.value)} /></Field></div>
              <div className="grid gap-4 sm:grid-cols-2"><Field label="协议"><select className="h-11 w-full rounded-[4px] border border-input bg-white px-3 text-sm" value={draft.protocol} onChange={(event) => update("protocol", event.target.value as RegistryDraft["protocol"])}>{Object.entries(PROTOCOL_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></Field><Field label="层级"><Input required value={draft.level} onChange={(event) => update("level", event.target.value)} /></Field><Field label="Base URL"><Input required value={draft.base_url} onChange={(event) => update("base_url", event.target.value)} /></Field><Field label="API 路径"><Input required value={draft.api_path} onChange={(event) => update("api_path", event.target.value)} /></Field><Field label="API Key"><Input type="password" value={draft.api_key} autoComplete="new-password" placeholder={editingId ? "留空保留当前密钥" : "请输入 API Key"} onChange={(event) => update("api_key", event.target.value)} /></Field><Field label="思考开关"><select className="h-11 w-full rounded-[4px] border border-input bg-white px-3 text-sm" value={draft.thinking_mode} onChange={(event) => update("thinking_mode", event.target.value as RegistryDraft["thinking_mode"])}><option value="auto">自动</option><option value="enabled">开启</option><option value="disabled">关闭</option></select></Field></div>
              <div className="grid gap-4 sm:grid-cols-3"><Field label="最大输出 Token"><Input type="number" min="128" value={draft.max_tokens} onChange={(event) => update("max_tokens", Number(event.target.value))} /></Field><Field label="输入 Token 上限"><Input type="number" min="0" value={draft.max_input_tokens} onChange={(event) => update("max_input_tokens", Number(event.target.value))} /></Field><Field label="超时（秒）"><Input type="number" min="10" value={draft.timeout_seconds} onChange={(event) => update("timeout_seconds", Number(event.target.value))} /></Field><Field label="最大并发"><Input type="number" min="1" value={draft.max_concurrency} onChange={(event) => update("max_concurrency", Number(event.target.value))} /></Field><Field label="每分钟调用上限"><Input type="number" min="0" value={draft.max_requests_per_minute} onChange={(event) => update("max_requests_per_minute", Number(event.target.value))} /></Field><Field label="月预算（micros）"><Input type="number" min="0" value={draft.monthly_budget_micros} onChange={(event) => update("monthly_budget_micros", Number(event.target.value))} /></Field><Field label="输入价格 / 百万 Token"><Input type="number" min="0" value={draft.input_micros_per_million_tokens} onChange={(event) => update("input_micros_per_million_tokens", Number(event.target.value))} /></Field><Field label="输出价格 / 百万 Token"><Input type="number" min="0" value={draft.output_micros_per_million_tokens} onChange={(event) => update("output_micros_per_million_tokens", Number(event.target.value))} /></Field><Field label="温度"><Input type="number" min="0" max="2" step="0.1" value={draft.temperature} onChange={(event) => update("temperature", Number(event.target.value))} /></Field></div>
              <label className="flex items-center justify-between gap-4 border-y border-[var(--line)] bg-[#f7f8f5] px-4 py-3 text-sm"><span><span className="font-semibold">结构化输出</span><span className="mt-1 block text-xs text-[var(--muted)]">服务端仍会校验响应结构。</span></span><input type="checkbox" checked={draft.structured_output} onChange={(event) => update("structured_output", event.target.checked)} /></label>
              <label className="flex items-center justify-between gap-4 border-y border-[var(--line)] bg-[#f7f8f5] px-4 py-3 text-sm"><span><span className="font-semibold">启用此注册项</span><span className="mt-1 block text-xs text-[var(--muted)]">停用只影响新任务，不删除历史快照。</span></span><input type="checkbox" checked={draft.active} onChange={(event) => update("active", event.target.checked)} /></label>
              <Field label="说明"><textarea className="min-h-24 w-full rounded-[4px] border border-[var(--line-strong)] px-3 py-2 text-sm" value={draft.description} onChange={(event) => update("description", event.target.value)} /></Field>
              <div className="flex justify-end gap-2 border-t border-[var(--line)] pt-5"><Button type="button" variant="secondary" onClick={() => { setDraft(null); setEditingId(null) }}>取消</Button><Button type="submit" disabled={save.isPending}><FloppyDisk />保存模型</Button></div>
            </form>
          </aside>
        </div>
      )}
    </>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block min-w-0"><span className="mb-2 block text-xs font-semibold">{label}</span>{children}</label>
}
