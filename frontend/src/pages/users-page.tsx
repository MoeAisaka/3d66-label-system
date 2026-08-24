import { useState } from "react"
import { FloppyDisk, Key, Plus, UserCircle, UserSwitch } from "@phosphor-icons/react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api, jsonBody } from "@/lib/api"
import type { User } from "@/lib/types"

const roles = [
  ["admin", "系统管理员"], ["manager", "项目管理员"], ["reviewer", "审核员"],
  ["analyst", "分析员"], ["viewer", "只读成员"],
] as const

export function UsersPage() {
  const client = useQueryClient()
  const users = useQuery({ queryKey: ["users"], queryFn: () => api<{ items: User[] }>("/api/users") })
  const [form, setForm] = useState({ username: "", display_name: "", password: "", role: "reviewer" })
  const [drafts, setDrafts] = useState<Record<number, { display_name: string; role: string; is_active: boolean; password: string }>>({})
  const draftFor = (user: User) => drafts[user.id] ?? { display_name: user.display_name, role: user.role ?? "viewer", is_active: user.is_active ?? true, password: "" }
  const create = useMutation({
    mutationFn: () => api<User>("/api/users", { method: "POST", ...jsonBody(form) }),
    onSuccess: async () => { setForm({ username: "", display_name: "", password: "", role: "reviewer" }); await client.invalidateQueries({ queryKey: ["users"] }); toast.success("账号已创建") },
    onError: (error) => toast.error(error.message),
  })
  const update = useMutation({
    mutationFn: ({ user, draft }: { user: User; draft: ReturnType<typeof draftFor> }) => api<User>(`/api/users/${user.id}`, { method: "PATCH", ...jsonBody({ display_name: draft.display_name, role: draft.role, is_active: draft.is_active, password: draft.password || null }) }),
    onSuccess: async (user) => { setDrafts((current) => { const next = { ...current }; delete next[user.id]; return next }); await client.invalidateQueries({ queryKey: ["users"] }); toast.success("账号权限已更新") },
    onError: (error) => toast.error(error.message),
  })
  return <>
    <PageHeader index="07" title="账号与权限" description="多人协作的账号、角色和最小权限管理。权限由服务端强制校验，前端隐藏不等于授权。" />
    <div className="mx-auto shell-content px-5 py-7 md:px-8 lg:px-10 lg:py-10">
      <section className="grid gap-7 border-y border-[var(--line-strong)] bg-white px-5 py-6 lg:grid-cols-[230px_1fr] lg:px-7">
        <div><div className="flex size-10 items-center justify-center rounded-[4px] bg-primary"><Plus size={21} weight="bold" /></div><h2 className="font-editorial mt-5 text-2xl font-bold">新增成员</h2><p className="mt-2 text-sm leading-6 text-[var(--muted)]">密码只在创建时提交，服务端仅保存 scrypt 哈希。</p></div>
        <div className="grid gap-4 md:grid-cols-2"><Input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} placeholder="登录账号" /><Input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} placeholder="显示名称" /><Input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} placeholder="初始密码（至少 10 位）" autoComplete="new-password" /><select className="h-11 rounded-[4px] border border-input bg-transparent px-3 text-sm" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>{roles.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><div className="md:col-span-2 flex justify-end"><Button onClick={() => create.mutate()} disabled={create.isPending || !form.username || !form.display_name || form.password.length < 10}><Plus />创建账号</Button></div></div>
      </section>
      <section className="mt-8 border-y border-[var(--line-strong)] bg-white"><div className="grid gap-7 border-b border-[var(--line)] px-5 py-6 lg:grid-cols-[230px_1fr] lg:px-7"><div><div className="flex size-10 items-center justify-center rounded-[4px] border border-[var(--line-strong)]"><UserSwitch size={21} /></div><h2 className="font-editorial mt-5 text-2xl font-bold">成员列表</h2></div><div className="divide-y divide-[var(--line)] border-y border-[var(--line)]">{(users.data?.items ?? []).map((user) => { const draft = draftFor(user); return <div key={user.id} className="grid gap-3 px-4 py-4 lg:grid-cols-[1fr_160px_190px_auto] lg:items-center"><div className="flex items-center gap-3"><UserCircle size={25} /><div><Input value={draft.display_name} onChange={(e) => setDrafts({ ...drafts, [user.id]: { ...draft, display_name: e.target.value } })} /><p className="font-data mt-1 text-xs text-[var(--muted)]">{user.username}</p></div></div><select className="h-10 rounded-[4px] border border-input bg-transparent px-2 text-sm" value={draft.role} onChange={(e) => setDrafts({ ...drafts, [user.id]: { ...draft, role: e.target.value } })}>{roles.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><div className="relative"><Key className="absolute left-3 top-3" size={16} /><Input className="pl-9" type="password" value={draft.password} onChange={(e) => setDrafts({ ...drafts, [user.id]: { ...draft, password: e.target.value } })} placeholder="留空则不重置密码" /></div><div className="flex gap-2"><Button variant="secondary" size="sm" onClick={() => setDrafts({ ...drafts, [user.id]: { ...draft, is_active: !draft.is_active } })}>{draft.is_active ? "停用" : "启用"}</Button><Button size="icon" title="保存账号设置" onClick={() => update.mutate({ user, draft })} disabled={update.isPending || !draft.display_name || (!!draft.password && draft.password.length < 10)}><FloppyDisk /></Button></div></div> })}</div></div></section>
    </div>
  </>
}
