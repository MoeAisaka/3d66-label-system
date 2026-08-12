import { useState, type ReactNode } from "react"
import {
  ArrowsClockwise,
  ChartLineUp,
  ListChecks,
  List,
  SignOut,
  SlidersHorizontal,
  SquaresFour,
  X,
} from "@phosphor-icons/react"
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom"
import { useMutation, useQueryClient } from "@tanstack/react-query"

import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import { AppVersion } from "@/lib/app-version"
import type { User } from "@/lib/types"
import { cn } from "@/lib/utils"

export const primaryWorkflowDomains = [
  {
    to: "/workflow/production-line",
    matches: [
      "/workflow/production-line",
      "/workflow/materials/packages",
      "/workflow/materials/assets",
      "/workflow/materials/jobs",
      "/assets",
    ],
    index: "01",
    label: "开始评测",
    icon: ArrowsClockwise,
    tabs: [
      { to: "/workflow/materials/packages", label: "1 导入素材" },
      { to: "/workflow/production-line", label: "2 选择素材包并评测" },
      { to: "/workflow/materials/jobs", label: "评测进度" },
    ],
  },
  {
    to: "/workflow/optimization/baseline-regression",
    matches: ["/workflow/optimization/baseline-regression"],
    index: "B",
    label: "存量回归",
    icon: ChartLineUp,
    tabs: [
      { to: "/workflow/optimization/baseline-regression", label: "基准回归与处理纠偏" },
    ],
  },
  {
    to: "/workflow/review/low-confidence",
    matches: [
      "/workflow/review/low-confidence",
      "/workflow/review/consensus",
      "/workflow/review/adjudication",
      "/workflow/review/completed",
      "/legacy/review",
    ],
    index: "02",
    label: "处理纠偏",
    icon: ListChecks,
    tabs: [
      { to: "/workflow/review/low-confidence", label: "待处理纠偏" },
      { to: "/workflow/review/consensus", label: "会审进度" },
      { to: "/workflow/review/adjudication", label: "主审处理" },
      { to: "/workflow/review/completed", label: "已完成" },
    ],
  },
  {
    to: "/workflow/releases/packages",
    matches: ["/workflow/releases/packages"],
    index: "03",
    label: "二审评测包",
    icon: SquaresFour,
    tabs: [
      { to: "/workflow/releases/packages", label: "待二审评测包" },
    ],
  },
] as const

const advancedWorkflowDomain =
  {
    to: "/workflow/governance",
    matches: [
      "/workflow/governance",
      "/workflow/optimization",
      "/workflow/models",
      "/workflow/releases/decisions",
      "/workflow/releases/metrics",
      "/workflow/releases/history",
      "/workflow/review/model-evaluation",
      "/legacy/sample-sets",
      "/legacy/historical-corrections",
    ],
    index: "A",
    label: "高级设置",
    icon: SlidersHorizontal,
  } as const

export const workflowDomains = [...primaryWorkflowDomains, advancedWorkflowDomain] as const

function domainMatches(pathname: string, matches: readonly string[]) {
  return matches.some((match) => pathname.startsWith(match))
}

export function AppShell({ user }: { user: User }) {
  const [open, setOpen] = useState(false)
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const logout = useMutation({
    mutationFn: () => api("/api/auth/logout", { method: "POST" }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["me"] })
      navigate("/login")
    },
  })
  const active = workflowDomains.find((item) => domainMatches(location.pathname, item.matches))

  return (
    <div className="min-h-[100dvh] bg-background text-foreground">
      <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b border-[var(--line)] bg-white/96 px-4 backdrop-blur-sm lg:hidden">
        <div className="flex items-baseline gap-2">
          <span className="font-editorial text-2xl font-bold">3d66</span>
          <span className="text-sm font-semibold">标签系统</span>
        </div>
        <Button variant="ghost" size="icon" aria-label="打开导航" onClick={() => setOpen(true)}>
          <List weight="bold" />
        </Button>
      </header>

      {open && (
        <button
          className="fixed inset-0 z-40 bg-black/20 lg:hidden"
          aria-label="关闭导航遮罩"
          onClick={() => setOpen(false)}
        />
      )}

      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 grid w-[252px] grid-cols-[72px_180px] border-r border-[var(--line)] bg-white transition-transform duration-200 ease-out lg:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex flex-col items-center bg-primary py-5 text-primary-foreground">
          <button
            className="flex size-11 items-center justify-center rounded-[4px] border border-black/20 font-editorial text-xl font-bold"
            aria-label="关闭导航"
            onClick={() => setOpen(false)}
          >
            <X className="lg:hidden" />
            <span className="hidden lg:block">3d</span>
          </button>
          <div className="mt-7 h-px w-7 bg-black/25" />
          <div className="mt-5 font-data text-2xl font-semibold">{active?.index ?? "01"}</div>
          <div className="mt-4 [writing-mode:vertical-rl] text-[0.65rem] font-semibold tracking-[0.16em]">
            3D66 LABEL SYSTEM
          </div>
          <div className="mt-auto grid gap-2 pb-1" aria-hidden="true">
            {Array.from({ length: 5 }, (_, index) => (
              <span key={index} className="h-px bg-black/35" style={{ width: `${8 + index * 3}px` }} />
            ))}
          </div>
        </div>

        <div className="flex min-w-0 flex-col">
          <div className="border-b border-[var(--line)] px-5 py-6">
            <p className="font-editorial text-[1.65rem] font-bold leading-none">标签系统</p>
            <p className="mt-2 text-xs text-[var(--muted)]">图片分类与美感评测</p>
          </div>
          <nav className="min-h-0 flex-1 overflow-y-auto py-3" aria-label="主导航">
            {primaryWorkflowDomains.map((item) => {
              const Icon = item.icon
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  onClick={() => setOpen(false)}
                  className={({ isActive }) =>
                    cn(
                      "group flex min-h-12 items-center gap-3 border-y border-transparent px-4 text-sm font-semibold text-[#4e544c] transition-colors",
                      domainMatches(location.pathname, item.matches) || isActive
                        ? "border-[var(--line)] bg-[#f7f9f3] text-foreground"
                        : "hover:bg-[#f8f9f6] hover:text-foreground",
                    )
                  }
                >
                  {() => {
                    const isDomainActive = domainMatches(location.pathname, item.matches)
                    return (
                    <>
                      <Icon size={19} weight={isDomainActive ? "fill" : "regular"} />
                      <span className="min-w-0 flex-1 truncate">{item.label}</span>
                      <span className="font-data text-[0.65rem] text-[#858c81]">{item.index}</span>
                    </>
                    )
                  }}
                </NavLink>
              )
            })}
          </nav>
          <div className="border-t border-[var(--line)] px-3 py-3">
            <NavLink
              to={advancedWorkflowDomain.to}
              onClick={() => setOpen(false)}
              className={cn(
                "flex min-h-11 items-center gap-3 px-2 text-sm font-semibold transition-colors",
                domainMatches(location.pathname, advancedWorkflowDomain.matches)
                  ? "bg-[#f7f9f3] text-foreground"
                  : "text-[#646a62] hover:bg-[#f8f9f6] hover:text-foreground",
              )}
            >
              <SlidersHorizontal size={18} />
              <span className="min-w-0 flex-1">高级设置</span>
              <span className="font-data text-[0.65rem] text-[#858c81]">A</span>
            </NavLink>
          </div>
          <div className="border-t border-[var(--line)] p-4">
            <div className="mb-3"><AppVersion /></div>
            <div className="mb-3 flex items-center gap-3">
              <div className="flex size-9 items-center justify-center rounded-[4px] bg-[#eef1eb] text-xs font-bold">
                {user.display_name.slice(0, 1)}
              </div>
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold">{user.display_name}</p>
                <p className="truncate text-xs text-[var(--muted)]">{user.username}</p>
              </div>
            </div>
            <Button
              variant="ghost"
              size="sm"
              className="w-full justify-start"
              onClick={() => logout.mutate()}
              disabled={logout.isPending}
            >
              <SignOut />退出登录
            </Button>
          </div>
        </div>
      </aside>

      <main className="min-h-[100dvh] min-w-0 lg:pl-[252px]">
        {active && "tabs" in active && active.tabs.length > 0 && (
          <nav
            className="sticky top-16 z-20 flex min-h-12 overflow-x-auto border-b border-[var(--line)] bg-white lg:top-0"
            aria-label={`${active.label}二级导航`}
          >
            {active.tabs.map((tab) => (
              <NavLink
                key={tab.to}
                to={tab.to}
                end={tab.to === active.to}
                className={({ isActive }) =>
                  cn(
                    "flex shrink-0 items-center border-r border-[var(--line)] px-5 py-3 text-xs font-bold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary",
                    isActive
                      ? "bg-primary text-primary-foreground"
                      : "text-[#555b53] hover:bg-[#f7f9f3] hover:text-foreground",
                  )
                }
              >
                {tab.label}
              </NavLink>
            ))}
          </nav>
        )}
        <Outlet />
      </main>
    </div>
  )
}

export function PageHeader({
  index,
  title,
  description,
  actions,
}: {
  index: string
  title: string
  description: string
  actions?: ReactNode
}) {
  return (
    <header className="border-b border-[var(--line)] bg-white px-5 py-6 md:px-8 lg:px-10">
      <div className="mx-auto flex max-w-[1540px] flex-wrap items-end justify-between gap-5">
        <div className="grid grid-cols-[auto_1fr] items-start gap-4">
          <span className="font-data mt-1 text-xs text-[var(--muted)]">{index}</span>
          <div>
            <h1 className="font-editorial text-[2rem] font-bold leading-[1.15] text-wrap-balance md:text-[2.35rem]">
              {title}
            </h1>
            <p className="mt-2 max-w-[70ch] text-sm leading-6 text-[var(--muted)]">{description}</p>
          </div>
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </header>
  )
}
