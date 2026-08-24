import { useState, type ReactNode } from "react"
import {
  ArrowsClockwise,
  ChartLineUp,
  GearSix,
  Images,
  ListChecks,
  List,
  SignOut,
  SlidersHorizontal,
  X,
} from "@phosphor-icons/react"
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom"
import { useMutation, useQueryClient } from "@tanstack/react-query"

import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import { AppVersion } from "@/lib/app-version"
import type { User } from "@/lib/types"
import { cn } from "@/lib/utils"

// 六个一级任务域，顺序即运营主链：待办 → 素材 → 增量 → 存量 → 纠偏发布 → 系统。
// 原先的运行中心、自动组批、质量资产、二审评测包不再占一级位置，按「这个能力服务于
// 哪条主线」下沉为二级：运行中心与自动组批服务增量产出，质量资产服务存量回归的基准，
// 二审评测包是纠偏的下游出口。
//
// matches 必须列具体路径，不能用 `/workflow/optimization` 这种泛化前缀——该前缀下
// 既有存量回归的页，也有归系统管理的评测机制管理，泛化会让两个域互相吞（详见
// 下方 active 的注释：域间前缀重叠会导致多个域同时点亮）。
export const primaryWorkflowDomains = [
  {
    to: "/workflow/dashboard",
    matches: ["/workflow/dashboard"],
    index: "00",
    label: "当前待办",
    icon: ListChecks,
    tabs: [{ to: "/workflow/dashboard", label: "今天需要处理什么" }],
  },
  {
    to: "/workflow/materials/packages",
    matches: ["/workflow/materials/packages", "/workflow/materials/assets", "/assets"],
    index: "01",
    label: "素材管理",
    icon: Images,
    tabs: [{ to: "/workflow/materials/packages", label: "素材包与导入" }],
  },
  {
    to: "/workflow/incremental",
    matches: [
      "/workflow/incremental",
      "/workflow/production-line",
      "/workflow/materials/jobs",
      "/workflow/operations",
      "/workflow/automation",
      "/workflow/optimization/automation",
      "/jobs",
    ],
    index: "02",
    label: "增量评测",
    icon: ArrowsClockwise,
    tabs: [
      { to: "/workflow/incremental", label: "增量工作台" },
      { to: "/workflow/production-line", label: "选择素材包并评测" },
      { to: "/workflow/materials/jobs", label: "评测进度" },
      { to: "/workflow/operations", label: "运行中心" },
      { to: "/workflow/automation", label: "自动组批" },
    ],
  },
  {
    to: "/workflow/stock",
    matches: [
      "/workflow/stock",
      "/workflow/optimization/baseline-regression",
      "/workflow/optimization/paired-regression",
      "/workflow/optimization/candidates",
      "/workflow/optimization/cases",
      "/workflow/optimization/feedback",
      "/workflow/quality-assets",
      "/legacy/sample-sets",
      "/legacy/historical-corrections",
      "/prompts",
      "/sample-sets",
      "/historical-corrections",
    ],
    index: "03",
    label: "存量回归",
    icon: ChartLineUp,
    tabs: [
      { to: "/workflow/stock", label: "存量工作台" },
      { to: "/workflow/optimization/baseline-regression", label: "基准回归与处理纠偏" },
      { to: "/workflow/optimization/paired-regression", label: "配对回归" },
      { to: "/workflow/optimization/candidates", label: "提示词候选" },
      { to: "/workflow/quality-assets", label: "质量资产" },
      { to: "/legacy/sample-sets", label: "完整样本库" },
    ],
  },
  {
    to: "/workflow/review/low-confidence",
    matches: [
      "/workflow/review",
      "/workflow/releases/packages",
      "/legacy/review",
      "/review",
    ],
    index: "04",
    label: "纠偏与发布",
    icon: SlidersHorizontal,
    tabs: [
      { to: "/workflow/review/low-confidence", label: "待处理纠偏" },
      { to: "/workflow/review/consensus", label: "会审进度" },
      { to: "/workflow/review/adjudication", label: "主审处理" },
      { to: "/workflow/review/completed", label: "已完成" },
      { to: "/workflow/releases/packages", label: "待二审评测包" },
    ],
  },
] as const

const advancedWorkflowDomain =
  {
    to: "/workflow/governance",
    matches: [
      "/workflow/governance",
      "/workflow/models",
      "/workflow/optimization/category-evaluation-preview",
      "/workflow/optimization/category-evaluation-v3-config",
      "/workflow/releases/decisions",
      "/workflow/releases/metrics",
      "/workflow/releases/history",
      "/workflow/review/model-evaluation",
      "/model",
      "/migrations",
      "/canary-runs",
    ],
    index: "A",
    label: "系统管理",
    icon: GearSix,
    tabs: [
      { to: "/workflow/governance", label: "系统管理首页" },
      { to: "/workflow/optimization/category-evaluation-v3-config", label: "类目评测 v3 合同配置" },
      { to: "/workflow/governance/model-registry", label: "模型登记" },
      { to: "/workflow/governance/users", label: "用户与权限" },
      { to: "/workflow/releases/history", label: "发布历史" },
    ],
  } as const

export const workflowDomains = [...primaryWorkflowDomains, advancedWorkflowDomain] as const

// 返回命中的最长前缀长度，未命中为 -1。
// 按路径分段比对（要么全等，要么后面紧跟 `/`），避免 `/assets` 误吞 `/assets-archive`
// 这类同前缀但不同段的路径。
function domainMatchLength(pathname: string, matches: readonly string[]) {
  let best = -1
  for (const match of matches) {
    if (pathname === match || pathname.startsWith(`${match}/`)) {
      best = Math.max(best, match.length)
    }
  }
  return best
}

// 取「最长匹配前缀」的域，而不是数组里第一个匹配的域。
// 域之间存在前缀重叠：纠偏与发布持有 `/workflow/review`，系统管理持有更具体的
// `/workflow/review/model-evaluation`。若按声明顺序取首个匹配，宽前缀会吞掉具体路径，
// 导致人在诊断页却显示「纠偏与发布」。最长前缀胜出让具体路径永远压过宽前缀，
// 与声明顺序无关。
function resolveActiveDomain(pathname: string) {
  let winner: (typeof workflowDomains)[number] | undefined
  let winnerScore = -1
  for (const item of workflowDomains) {
    const score = domainMatchLength(pathname, item.matches)
    if (score > winnerScore) {
      winner = item
      winnerScore = score
    }
  }
  return winnerScore >= 0 ? winner : undefined
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
  const active = resolveActiveDomain(location.pathname)

  // 面包屑第二段。精确命中优先；再退到最长前缀，用于 `/…/packages/:id` 这类详情页。
  // 前缀匹配必须排除「域首页」那一项（它的 to 等于域自己的 to），否则该域下每个深层页
  // 都会被它前缀吃掉，显示成「系统管理首页」。
  // 都不命中时返回 undefined，面包屑只显示域名，不硬凑第二段。
  const currentTab = active
    ? active.tabs.find((tab) => tab.to === location.pathname) ??
      active.tabs
        .filter((tab) => tab.to !== active.to && location.pathname.startsWith(`${tab.to}/`))
        .sort((a, b) => b.to.length - a.to.length)[0]
    : undefined

  return (
    <div className="min-h-[100dvh] bg-background text-foreground">
      <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b border-[var(--line)] bg-white/96 px-4 backdrop-blur-sm lg:hidden">
        <div className="flex items-baseline gap-2">
          <span className="font-editorial text-2xl font-bold">特鹏</span>
          <span className="text-sm font-semibold">标签中台</span>
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
            <span className="hidden lg:block">LL</span>
          </button>
          <div className="mt-7 h-px w-7 bg-black/25" />
          <div className="mt-5 font-data text-2xl font-semibold">{active?.index ?? "01"}</div>
          <div className="mt-4 [writing-mode:vertical-rl] text-[0.65rem] font-semibold tracking-[0.16em]">
            特鹏 LABEL SYSTEM
          </div>
          <div className="mt-auto grid gap-2 pb-1" aria-hidden="true">
            {Array.from({ length: 5 }, (_, index) => (
              <span key={index} className="h-px bg-black/35" style={{ width: `${8 + index * 3}px` }} />
            ))}
          </div>
        </div>

        <div className="flex min-w-0 flex-col">
          <div className="border-b border-[var(--line)] px-5 py-6">
            <p className="font-editorial text-[1.45rem] font-bold leading-tight">特鹏标签中台</p>
            <p className="mt-2 text-xs text-[var(--muted)]">Label System · 标签与内容中台底座</p>
          </div>
          {/* 六个域同列同款式的胶囊项。系统管理原先是底部独立段落、样式也与主导航不同，
              但它在 IA 里就是第六个任务域，视觉降级会让人以为它不是常规入口。
              二级页面收纳在所属一级域下方，且只在该域为当前域时展开——任何时刻侧栏里
              只有一组二级项，既看得清「我在哪个域的哪一页」，又不会六个域全摊开。
              只有一个二级项的域不展开（那一项等于域本身，展开是重复信息）。 */}
          <nav className="min-h-0 flex-1 overflow-y-auto px-2 py-2.5" aria-label="主导航">
            {workflowDomains.map((item) => {
              const Icon = item.icon
              const isDomainActive = active?.to === item.to
              const showChildren = isDomainActive && item.tabs.length > 1
              return (
                <div key={item.to}>
                  <NavLink
                    to={item.to}
                    onClick={() => setOpen(false)}
                    className={cn(
                      "flex min-h-11 items-center gap-2.5 rounded-[6px] px-3 text-[0.845rem] transition-colors",
                      isDomainActive
                        ? "bg-[var(--active-surface)] font-bold text-[var(--active-text)]"
                        : "font-semibold text-[var(--muted)] hover:bg-[#f8f9f6] hover:text-foreground",
                    )}
                  >
                    <Icon size={18} weight={isDomainActive ? "fill" : "regular"} />
                    <span className="min-w-0 flex-1 truncate">{item.label}</span>
                    <span
                      className={cn(
                        "font-data text-[0.625rem]",
                        isDomainActive ? "text-[var(--active-text)]" : "text-[var(--muted-soft)]",
                      )}
                    >
                      {item.index}
                    </span>
                  </NavLink>

                  {showChildren && (
                    <div className="pb-1.5 pl-3 pt-0.5" role="group" aria-label={`${item.label}二级页面`}>
                      {item.tabs.map((tab) => (
                        <NavLink
                          key={tab.to}
                          to={tab.to}
                          end={tab.to === item.to}
                          onClick={() => setOpen(false)}
                          className={({ isActive }) =>
                            cn(
                              "flex min-h-[30px] items-center gap-2 rounded-[4px] pl-2.5 pr-2 text-xs transition-colors",
                              isActive
                                ? "bg-[#f7f9f3] font-bold text-foreground"
                                : "text-[var(--muted)] hover:bg-[#f8f9f6] hover:text-foreground",
                            )
                          }
                        >
                          {({ isActive }) => (
                            <>
                              {/* 引导线：表达「隶属上方那个一级域」，比单纯缩进更明确 */}
                              <span
                                aria-hidden="true"
                                className={cn(
                                  "h-3.5 w-0.5 shrink-0",
                                  isActive ? "bg-[var(--active-border)]" : "bg-[var(--line-strong)]",
                                )}
                              />
                              <span className="min-w-0 flex-1 truncate">{tab.label}</span>
                            </>
                          )}
                        </NavLink>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </nav>
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
        {active && (
          // 二级页面已收纳进侧栏，这里不再重复平铺一排 tab——同一组链接出现两次，
          // 人得先判断该点哪一处。改成面包屑：只回答「我在哪」，不承担跳转选择。
          <div className="sticky top-16 z-20 flex min-h-12 items-center gap-2 border-b border-[var(--line)] bg-white px-5 md:px-8 lg:top-0 lg:px-10">
            <nav aria-label="面包屑" className="flex min-w-0 items-center gap-2">
              <span className="font-data shrink-0 text-[0.625rem] text-[var(--muted-soft)]">{active.index}</span>
              <NavLink
                to={active.to}
                className="shrink-0 text-xs font-semibold text-[var(--muted)] transition-colors hover:text-foreground"
              >
                {active.label}
              </NavLink>
              {currentTab && (
                <>
                  <span aria-hidden="true" className="shrink-0 text-xs text-[var(--muted-soft)]">
                    /
                  </span>
                  <span aria-current="page" className="min-w-0 truncate text-xs font-bold text-foreground">
                    {currentTab.label}
                  </span>
                </>
              )}
            </nav>
          </div>
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
      <div className="mx-auto flex shell-content flex-wrap items-end justify-between gap-5">
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
