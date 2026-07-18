import { useEffect, useMemo, useState } from "react"
import { ArrowClockwise, Check, MagicWand, Plus, UploadSimple } from "@phosphor-icons/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { api, jsonBody } from "@/lib/api"
import type { PromptVersion } from "@/lib/types"

export function PromptsPage() {
  const queryClient = useQueryClient()
  const prompts = useQuery({
    queryKey: ["prompts"],
    queryFn: () => api<{ items: PromptVersion[] }>("/api/prompts"),
  })
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const selected = useMemo(
    () => prompts.data?.items.find((item) => item.id === selectedId) ?? prompts.data?.items[0],
    [prompts.data?.items, selectedId],
  )
  const [systemPrompt, setSystemPrompt] = useState("")
  const [userPrompt, setUserPrompt] = useState("")
  const [version, setVersion] = useState("")
  const [changeNote, setChangeNote] = useState("")
  const [aiInstruction, setAiInstruction] = useState("")

  useEffect(() => {
    if (!selected) return
    setSystemPrompt(selected.system_prompt)
    setUserPrompt(selected.user_prompt)
    setVersion(`${selected.version}-draft`)
    setChangeNote("")
  }, [selected?.id])

  const create = useMutation({
    mutationFn: () =>
      api<{ id: number }>("/api/prompts", {
        method: "POST",
        ...jsonBody({
          stage: selected?.stage,
          name: selected?.name,
          version,
          system_prompt: systemPrompt,
          user_prompt: userPrompt,
          rubric_version: selected?.rubric_version,
          change_note: changeNote,
          source: "manual",
        }),
      }),
    onSuccess: async (data) => {
      await queryClient.invalidateQueries({ queryKey: ["prompts"] })
      setSelectedId(data.id)
      toast.success("已保存为草稿版本，不会自动生效")
    },
    onError: (error) => toast.error(error.message),
  })
  const publish = useMutation({
    mutationFn: () => api(`/api/prompts/${selected?.id}/publish`, { method: "POST" }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["prompts"] })
      toast.success("提示词版本已发布")
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

  return (
    <>
      <PageHeader
        index="05"
        title="提示词工作室"
        description="AI 只能生成修改草案；新版本必须保存、评测并由人工发布，线上版本不会被直接覆盖。"
        actions={
          <>
            <Button variant="secondary" onClick={() => prompts.refetch()}><ArrowClockwise />刷新</Button>
            <Button onClick={() => create.mutate()} disabled={!selected || !version || create.isPending}><Plus />另存草稿</Button>
          </>
        }
      />
      <div className="mx-auto grid max-w-[1720px] lg:grid-cols-[280px_minmax(0,1fr)]">
        <aside className="border-r border-[var(--line)] bg-white p-4 lg:min-h-[calc(100dvh-125px)]">
          <div className="mb-4 flex items-center justify-between"><h2 className="text-sm font-semibold">版本</h2><span className="font-data text-xs text-[var(--muted)]">{prompts.data?.items.length ?? 0}</span></div>
          <div className="space-y-1">
            {prompts.data?.items.map((prompt) => (
              <button
                key={prompt.id}
                className={`w-full rounded-[4px] border px-3 py-3 text-left transition-colors ${selected?.id === prompt.id ? "border-[var(--line-strong)] bg-[#f6f9dc]" : "border-transparent hover:bg-[#f8f9f6]"}`}
                onClick={() => setSelectedId(prompt.id)}
              >
                <div className="flex items-center justify-between gap-2"><span className="font-data text-xs font-semibold">调用 {prompt.stage}</span><Badge tone={prompt.status === "published" ? "active" : prompt.status === "draft" ? "warning" : "neutral"}>{prompt.status}</Badge></div>
                <p className="mt-2 truncate text-sm font-semibold">{prompt.version}</p>
                <p className="mt-1 line-clamp-2 text-xs leading-5 text-[var(--muted)]">{prompt.change_note || prompt.name}</p>
              </button>
            ))}
          </div>
        </aside>

        {selected ? (
          <main className="min-w-0 px-5 py-7 md:px-8 lg:px-10 lg:py-9">
            <div className="flex flex-wrap items-start justify-between gap-5 border-b border-[var(--line-strong)] pb-6">
              <div><p className="font-data text-xs text-[var(--muted)]">调用 {selected.stage} · {selected.rubric_version}</p><h2 className="font-editorial mt-2 text-3xl font-bold">{selected.name}</h2><p className="mt-2 text-sm text-[var(--muted)]">当前选择：{selected.version}，创建者 {selected.created_by}</p></div>
              {selected.status !== "published" && <Button onClick={() => publish.mutate()} disabled={publish.isPending}><UploadSimple />发布此版本</Button>}
            </div>

            <section className="mt-7 grid gap-4 border-y border-[var(--line-strong)] bg-white p-4 xl:grid-cols-[minmax(0,1fr)_280px] xl:p-5">
              <div>
                <div className="flex items-center gap-2"><MagicWand size={20} /><h3 className="font-semibold">让 AI 提议修改</h3></div>
                <p className="mt-1 text-xs leading-5 text-[var(--muted)]">AI 返回的内容只会进入下方编辑器，不写入数据库，也不会发布。</p>
                <Textarea className="mt-3 min-h-24" value={aiInstruction} onChange={(event) => setAiInstruction(event.target.value)} placeholder="例如：保留输出结构，增强对局部空间和艺术性景深的容错说明" />
              </div>
              <div className="flex items-end"><Button className="w-full" onClick={() => aiRevise.mutate()} disabled={!aiInstruction || aiRevise.isPending}>{aiRevise.isPending ? "AI 正在生成草案" : "生成修改草案"}<MagicWand /></Button></div>
            </section>

            <div className="mt-8 grid gap-7 2xl:grid-cols-2">
              <label className="block"><span className="mb-2 flex items-center justify-between text-sm font-semibold"><span>System Prompt</span><span className="font-data text-xs font-normal text-[var(--muted)]">{systemPrompt.length} 字符</span></span><Textarea className="min-h-[520px] font-mono text-[0.78rem] leading-6" value={systemPrompt} onChange={(event) => setSystemPrompt(event.target.value)} /></label>
              <label className="block"><span className="mb-2 flex items-center justify-between text-sm font-semibold"><span>User Prompt</span><span className="font-data text-xs font-normal text-[var(--muted)]">{userPrompt.length} 字符</span></span><Textarea className="min-h-[520px] font-mono text-[0.78rem] leading-6" value={userPrompt} onChange={(event) => setUserPrompt(event.target.value)} /></label>
            </div>

            <section className="mt-8 border-y border-[var(--line-strong)] bg-white p-5">
              <h3 className="font-editorial text-xl font-bold">保存新版本</h3>
              <div className="mt-4 grid gap-4 lg:grid-cols-[260px_1fr_auto]">
                <label><span className="mb-2 block text-xs font-semibold">版本号</span><Input value={version} onChange={(event) => setVersion(event.target.value)} /></label>
                <label><span className="mb-2 block text-xs font-semibold">修改说明</span><Input value={changeNote} onChange={(event) => setChangeNote(event.target.value)} placeholder="说明修改目的和预期影响" /></label>
                <div className="flex items-end"><Button onClick={() => create.mutate()} disabled={!version || create.isPending}><Check />保存草稿</Button></div>
              </div>
            </section>
          </main>
        ) : <div className="min-h-[60dvh] animate-pulse bg-white" />}
      </div>
    </>
  )
}
