import {
  ArrowRight,
  Brain,
  Database,
  GearSix,
  ShieldCheck,
  SlidersHorizontal,
} from "@phosphor-icons/react"
import { Link } from "react-router-dom"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import type { User } from "@/lib/types"

const managementGroups = [
  {
    title: "评测方案",
    description: "维护类目评测 v3 合同、提示词和模型配置。普通审核员开始评测时无需配置这些内容。",
    icon: SlidersHorizontal,
    entries: [
      { to: "/workflow/optimization/category-evaluation-preview", label: "类目评测底座预览", note: "查看四类目的评测合同、校验状态和运行边界" },
      { to: "/workflow/optimization/category-evaluation-v3-config", label: "类目评测 v3 合同配置", note: "四类目的分类赛道与子类目维度唯一配置入口" },
      { to: "/workflow/optimization/candidates", label: "提示词版本", note: "查看候选、变更说明和人工发布门禁" },
      { to: "/workflow/governance/model-registry", label: "模型注册中心", note: "列表维护主模型、调优模型、协议和调用限制" },
      { to: "/workflow/governance/tag-demand-contracts", label: "字段需求合同", note: "维护平台通用语义字段、类目适用性和质量门槛版本" },
    ],
  },
  {
    title: "自动优化与验证",
    description: "查看一审纠偏如何形成改进候选，以及候选在发布前的验证证据。",
    icon: Brain,
    entries: [
      { to: "/workflow/optimization/cases", label: "纠偏案例池", note: "查看人工纠偏和生产回流的可追溯案例" },
      { to: "/workflow/optimization/automation", label: "预算、协议与执行器", note: "管理员维护组批预算、运行协议、执行器和失败恢复参数" },
      { to: "/workflow/optimization/paired-regression", label: "配对回归", note: "比较候选与当前版本的冻结小样本证据" },
      { to: "/workflow/optimization/baseline-regression", label: "基准回归", note: "使用冻结基准集检查准确率和逐张偏差" },
      { to: "/workflow/optimization/feedback", label: "生产案例回流", note: "查看外部系统回流的最终纠偏事件" },
    ],
  },
  {
    title: "模型实验与迁移",
    description: "面向管理员和算法人员的模型对比、迁移及生产候选证据。",
    icon: Database,
    entries: [
      { to: "/workflow/models/benchmark", label: "多模型横评", note: "冻结同一批样本比较质量、稳定性和成本" },
      { to: "/workflow/models/migration", label: "模型迁移", note: "用历史结果和人工真值评估新模型" },
      { to: "/workflow/models/candidates", label: "生产候选", note: "查看可进入生产决策的模型组合" },
    ],
  },
  {
    title: "账号、安全与追溯",
    description: "管理访问权限，并检查关键自动化和人工动作的完整记录。",
    icon: ShieldCheck,
    entries: [
      { to: "/workflow/governance/users", label: "账号与权限", note: "分配审核和管理权限" },
      { to: "/workflow/governance/canary", label: "受控试运行", note: "在不改写生产数据的前提下验证候选流程" },
      { to: "/workflow/governance/audit", label: "系统审计", note: "查看只追加的关键操作记录" },
      { to: "/legacy/historical-corrections", label: "历史纠偏导入", note: "预览历史资料，不直接形成黄金真值" },
    ],
  },
  {
    title: "发布与历史证据",
    description: "二审之后的正式发布、版本指标和历史记录集中在这里，不占用一线审核主导航。",
    icon: Database,
    entries: [
      { to: "/workflow/releases/decisions", label: "正式标签发布", note: "对已通过二审的评测包执行独立发布决定" },
      { to: "/workflow/releases/metrics", label: "版本指标", note: "查看正式版本的质量与运行指标" },
      { to: "/workflow/releases/history", label: "发布历史", note: "追溯每次发布、回滚和人工决定" },
      { to: "/workflow/governance/projections", label: "下游表投影", note: "维护统一大维表和职责小表的版本合同与本地对账" },
      { to: "/workflow/review/model-evaluation", label: "全部评测结果", note: "面向诊断人员查看非默认的完整运行明细" },
      { to: "/legacy/sample-sets", label: "黄金样本集管理", note: "维护锁定真值和回归样本组成" },
    ],
  },
] as const

export function SystemManagementPage({ user }: { user: User }) {
  return (
    <>
      <PageHeader
        index="A.1"
        title="高级设置"
        description="一线审核流程之外的配置、实验和追溯能力集中在这里。生产线默认跟随已确认的类目方案，只有管理员需要进入这些入口。"
        actions={<Badge tone={user.is_admin ? "success" : "neutral"}>{user.is_admin ? "管理员权限" : "只读查看"}</Badge>}
      />
      <div className="mx-auto max-w-[1540px] px-5 py-8 md:px-8 lg:px-10">
        {!user.is_admin && (
          <div className="mb-7 flex items-start gap-3 border-y border-[var(--line-strong)] bg-[#fff9e9] px-5 py-4 text-sm leading-6 text-[#6f5513]">
            <GearSix className="mt-0.5 shrink-0" />
            <p>当前账号可以查看系统能力，但修改运行方案或发布配置时会由系统拦截。需要调整时请联系管理员。</p>
          </div>
        )}
        <div className="space-y-8">
          {managementGroups.map((group) => {
            const Icon = group.icon
            return (
              <section key={group.title} className="border-y border-[var(--line-strong)] bg-white">
                <div className="grid gap-3 border-b border-[var(--line)] px-5 py-5 md:grid-cols-[220px_minmax(0,1fr)]">
                  <div className="flex items-center gap-3"><Icon size={22} /><h2 className="font-editorial text-xl font-bold">{group.title}</h2></div>
                  <p className="text-sm leading-6 text-[var(--muted)]">{group.description}</p>
                </div>
                <div className="divide-y divide-[var(--line)]">
                  {group.entries.map((entry) => (
                    <Link
                      key={entry.to}
                      to={entry.to}
                      className="grid gap-2 px-5 py-4 transition-colors hover:bg-[#f8f9f6] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary sm:grid-cols-[220px_minmax(0,1fr)_auto] sm:items-center"
                    >
                      <span className="text-sm font-bold">{entry.label}</span>
                      <span className="text-xs leading-5 text-[var(--muted)]">{entry.note}</span>
                      <ArrowRight aria-hidden="true" />
                    </Link>
                  ))}
                </div>
              </section>
            )
          })}
        </div>
      </div>
    </>
  )
}
