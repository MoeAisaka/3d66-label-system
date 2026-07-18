import { useState } from "react"
import { ArrowRight, CheckCircle, ImageSquare } from "@phosphor-icons/react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Navigate, useNavigate } from "react-router-dom"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api, jsonBody } from "@/lib/api"
import type { User } from "@/lib/types"

export function LoginPage({ user }: { user?: User }) {
  const [username, setUsername] = useState("sol")
  const [password, setPassword] = useState("")
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const login = useMutation({
    mutationFn: () =>
      api<User>("/api/auth/login", {
        method: "POST",
        ...jsonBody({ username, password }),
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(["me"], data)
      navigate("/")
    },
  })

  if (user) return <Navigate to="/" replace />

  return (
    <main className="grid min-h-[100dvh] bg-white lg:grid-cols-[minmax(0,1.12fr)_minmax(420px,.88fr)]">
      <section className="relative hidden overflow-hidden border-r border-[var(--line)] bg-[#f3f5f0] p-10 lg:flex lg:flex-col">
        <div className="absolute inset-y-0 left-0 w-16 bg-primary" />
        <div className="ml-16 flex items-center gap-3 text-xs font-semibold tracking-[0.12em]">
          <span className="font-data">3D66</span>
          <span className="h-px w-12 bg-[#aeb5a9]" />
          <span>LABEL SYSTEM</span>
        </div>
        <div className="ml-16 mt-auto max-w-3xl pb-10">
          <p className="font-data mb-5 text-xs text-[var(--muted)]">01 / VISUAL INTELLIGENCE</p>
          <h1 className="font-editorial max-w-[10ch] text-[clamp(4rem,7.2vw,7.5rem)] font-bold leading-[0.98] tracking-[-0.045em]">
            让每张图片的质量可解释
          </h1>
          <p className="mt-7 max-w-[54ch] text-base leading-7 text-[#51574f]">
            分类、画质预检、美感维度和人工复核在同一条证据链中完成。
          </p>
          <div className="mt-9 grid max-w-2xl grid-cols-3 border-y border-[var(--line-strong)] py-5">
            <div className="border-r border-[var(--line)] pr-5">
              <CheckCircle size={22} />
              <p className="mt-4 text-sm font-semibold">两阶段评测</p>
            </div>
            <div className="border-r border-[var(--line)] px-5">
              <ImageSquare size={22} />
              <p className="mt-4 text-sm font-semibold">白底审图</p>
            </div>
            <div className="pl-5">
              <span className="font-data text-xl font-semibold">V2.1</span>
              <p className="mt-4 text-sm font-semibold">提示词版本化</p>
            </div>
          </div>
        </div>
      </section>

      <section className="relative flex items-center justify-center p-5 sm:p-10">
        <div className="absolute left-0 top-0 h-2 w-full bg-primary lg:hidden" />
        <div className="w-full max-w-[420px]">
          <div className="mb-10 flex items-baseline gap-3">
            <span className="font-editorial text-4xl font-bold">3d66</span>
            <span className="text-sm font-semibold">标签系统</span>
          </div>
          <p className="font-data text-xs text-[var(--muted)]">SECURE SIGN IN</p>
          <h2 className="font-editorial mt-3 text-4xl font-bold leading-tight">登录评测工作台</h2>
          <p className="mt-3 text-sm leading-6 text-[var(--muted)]">使用管理员账号进入。审核员可在进入后填写自己的审核姓名。</p>

          <form
            className="mt-9 space-y-5"
            onSubmit={(event) => {
              event.preventDefault()
              login.mutate()
            }}
          >
            <label className="block">
              <span className="mb-2 block text-sm font-semibold">账号</span>
              <Input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" />
            </label>
            <label className="block">
              <span className="mb-2 block text-sm font-semibold">密码</span>
              <Input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="current-password"
                placeholder="请输入密码"
              />
            </label>
            {login.error && (
              <p role="alert" className="rounded-[4px] border border-[#e8c1bd] bg-[#fff0ee] px-3 py-2 text-sm text-[#8d2924]">
                {(login.error as Error).message}
              </p>
            )}
            <Button type="submit" className="w-full justify-between" disabled={login.isPending || !password}>
              {login.isPending ? "正在登录" : "进入工作台"}
              <ArrowRight weight="bold" />
            </Button>
          </form>
          <p className="mt-8 border-t border-[var(--line)] pt-5 text-xs leading-5 text-[var(--muted)]">
            Demo 数据只保存在当前电脑。更换电脑后重新配置模型并重新跑图片即可。
          </p>
        </div>
      </section>
    </main>
  )
}
