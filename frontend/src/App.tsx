import { lazy, Suspense } from "react"
import { ArrowClockwise, WarningCircle } from "@phosphor-icons/react"
import { useQuery } from "@tanstack/react-query"
import { Navigate, Route, Routes, useParams } from "react-router-dom"
import { Toaster } from "sonner"

import { AppShell } from "@/components/app-shell"
import { Button } from "@/components/ui/button"
import { api, ApiError } from "@/lib/api"
import type { User } from "@/lib/types"
import { AssetsPage } from "@/pages/assets-page"
import { JobsPage } from "@/pages/jobs-page"
import { LoginPage } from "@/pages/login-page"
import { ModelPage } from "@/pages/model-page"
import { MigrationsPage } from "@/pages/migrations-page"
import { UsersPage } from "@/pages/users-page"

const SampleSetsPage = lazy(() =>
  import("@/pages/sample-sets-page").then((module) => ({ default: module.SampleSetsPage })),
)
const PromptCandidatesPage = lazy(() =>
  import("@/pages/prompts-page").then((module) => ({ default: module.PromptCandidatesPage })),
)
const PairedRegressionPage = lazy(() =>
  import("@/pages/paired-regression-page").then((module) => ({ default: module.PairedRegressionPage })),
)
const BaselineRegressionPage = lazy(() =>
  import("@/pages/baseline-regression-page").then((module) => ({ default: module.BaselineRegressionPage })),
)
const DimensionManagerPage = lazy(() =>
  import("@/pages/dimension-manager-page").then((module) => ({ default: module.DimensionManagerPage })),
)
const ReviewPage = lazy(() =>
  import("@/pages/review-page").then((module) => ({ default: module.ReviewPage })),
)
const CanaryRunsPage = lazy(() =>
  import("@/pages/canary-runs-page").then((module) => ({ default: module.CanaryRunsPage })),
)
const CategoryEvaluationPreviewPage = lazy(() =>
  import("@/pages/category-evaluation-preview-page").then((module) => ({ default: module.CategoryEvaluationPreviewPage })),
)
const CategoryEvaluationV3ConfigPage = lazy(() =>
  import("@/pages/category-evaluation-v3-config-page").then((module) => ({ default: module.CategoryEvaluationV3ConfigPage })),
)
const HistoricalCorrectionsPage = lazy(() =>
  import("@/pages/historical-corrections-page").then((module) => ({ default: module.HistoricalCorrectionsPage })),
)
const OptimizationCasesPage = lazy(() =>
  import("@/pages/workflow-pages").then((module) => ({ default: module.OptimizationCasesPage })),
)
const AutomationControlPage = lazy(() =>
  import("@/pages/workflow-pages").then((module) => ({ default: module.AutomationControlPage })),
)
const ProductionFeedbackPage = lazy(() =>
  import("@/pages/workflow-pages").then((module) => ({ default: module.ProductionFeedbackPage })),
)
const BenchmarkPage = lazy(() =>
  import("@/pages/workflow-pages").then((module) => ({ default: module.BenchmarkPage })),
)
const AuditEventsPage = lazy(() =>
  import("@/pages/workflow-pages").then((module) => ({ default: module.AuditEventsPage })),
)
const ReleaseWorkspacePage = lazy(() =>
  import("@/pages/workflow-pages").then((module) => ({ default: module.ReleaseWorkspacePage })),
)
const CapabilityStatusPage = lazy(() =>
  import("@/pages/workflow-pages").then((module) => ({ default: module.CapabilityStatusPage })),
)
const EvaluationPackagePipelinePage = lazy(() =>
  import("@/pages/evaluation-packages-page").then((module) => ({ default: module.EvaluationPackagePipelinePage })),
)
const EvaluationPackageReviewListPage = lazy(() =>
  import("@/pages/evaluation-packages-page").then((module) => ({ default: module.EvaluationPackageReviewListPage })),
)
const EvaluationPackageDetailPage = lazy(() =>
  import("@/pages/evaluation-packages-page").then((module) => ({ default: module.EvaluationPackageDetailPage })),
)
const SystemManagementPage = lazy(() =>
  import("@/pages/system-management-page").then((module) => ({ default: module.SystemManagementPage })),
)

export default function App() {
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => api<User>("/api/auth/me"),
    retry: false,
  })

  if (me.isLoading) {
    return (
      <div className="flex min-h-[100dvh] items-center justify-center bg-white">
        <div className="text-center"><div className="font-editorial text-5xl font-bold">3d66</div><div className="mx-auto mt-5 h-1 w-28 overflow-hidden bg-[#eef1eb]"><div className="status-pulse h-full w-1/2 bg-primary" /></div><p className="mt-4 text-sm text-[var(--muted)]">正在进入标签系统</p></div>
      </div>
    )
  }

  const unauthenticated = me.error instanceof ApiError && me.error.status === 401
  if (me.isError && !unauthenticated) {
    const permissionDenied = me.error instanceof ApiError && me.error.status === 403
    return (
      <div className="flex min-h-[100dvh] items-center justify-center bg-[#f3f5f0] px-5">
        <div className="w-full max-w-lg border-y border-[var(--line-strong)] bg-white px-6 py-8 text-center">
          <WarningCircle className="mx-auto text-[#a85a0a]" size={34} weight="fill" />
          <h1 className="font-editorial mt-4 text-2xl font-bold">
            {permissionDenied ? "当前账号无法进入标签系统" : "暂时无法连接标签系统"}
          </h1>
          <p className="mt-3 text-sm leading-6 text-[var(--muted)]">
            {permissionDenied
              ? "请联系管理员确认账号权限，或使用具备访问权限的账号重新登录。"
              : "请检查网络和服务状态。连接恢复后可以直接重试，不会影响已经保存的记录。"}
          </p>
          <Button className="mt-6" onClick={() => me.refetch()}><ArrowClockwise />重新连接</Button>
        </div>
      </div>
    )
  }
  const user = me.data

  return (
    <>
      <Routes>
        <Route path="/login" element={<LoginPage user={user} />} />
        {user ? (
          <Route element={<AppShell user={user} />}>
            <Route index element={<Navigate to="/workflow/production-line" replace />} />
            <Route path="workflow/production-line" element={<Suspense fallback={<RouteLoading />}><EvaluationPackagePipelinePage user={user} /></Suspense>} />
            <Route path="workflow/materials/packages" element={<AssetsPage />} />
            <Route path="workflow/materials/assets" element={<Navigate to="/workflow/materials/packages" replace />} />
            <Route path="workflow/materials/jobs" element={<JobsPage />} />
            <Route path="workflow/review/:reviewView" element={<Suspense fallback={<RouteLoading />}><ReviewPage user={user} /></Suspense>} />
            <Route path="workflow/optimization/cases" element={<Suspense fallback={<RouteLoading />}><OptimizationCasesPage /></Suspense>} />
            <Route path="workflow/optimization/automation" element={<Suspense fallback={<RouteLoading />}><AutomationControlPage /></Suspense>} />
            <Route path="workflow/optimization/feedback" element={<Suspense fallback={<RouteLoading />}><ProductionFeedbackPage /></Suspense>} />
            <Route path="workflow/optimization/candidates" element={<Suspense fallback={<RouteLoading />}><PromptCandidatesPage /></Suspense>} />
            <Route path="workflow/optimization/dimensions" element={<Suspense fallback={<RouteLoading />}><DimensionManagerPage /></Suspense>} />
            <Route path="workflow/optimization/category-evaluation-preview" element={<Suspense fallback={<RouteLoading />}><CategoryEvaluationPreviewPage /></Suspense>} />
            <Route path="workflow/optimization/category-evaluation-v3-config" element={<Suspense fallback={<RouteLoading />}><CategoryEvaluationV3ConfigPage /></Suspense>} />
            <Route path="workflow/optimization/paired-regression" element={<Suspense fallback={<RouteLoading />}><PairedRegressionPage user={user} /></Suspense>} />
            <Route path="workflow/optimization/baseline-regression" element={<Suspense fallback={<RouteLoading />}><BaselineRegressionPage /></Suspense>} />
            <Route path="workflow/releases/packages" element={<Suspense fallback={<RouteLoading />}><EvaluationPackageReviewListPage /></Suspense>} />
            <Route path="workflow/releases/packages/:packageId" element={<Suspense fallback={<RouteLoading />}><EvaluationPackageDetailPage /></Suspense>} />
            <Route path="workflow/releases/decisions" element={<Suspense fallback={<RouteLoading />}><ReleaseWorkspacePage view="decisions" /></Suspense>} />
            <Route path="workflow/releases/metrics" element={<Suspense fallback={<RouteLoading />}><ReleaseWorkspacePage view="metrics" /></Suspense>} />
            <Route path="workflow/releases/history" element={<Suspense fallback={<RouteLoading />}><ReleaseWorkspacePage view="history" /></Suspense>} />
            <Route path="workflow/models/benchmark" element={<Suspense fallback={<RouteLoading />}><BenchmarkPage /></Suspense>} />
            <Route path="workflow/models/migration" element={<MigrationsPage user={user} />} />
            <Route path="workflow/models/candidates" element={<Suspense fallback={<RouteLoading />}><CapabilityStatusPage kind="candidates" /></Suspense>} />
            <Route path="workflow/governance" element={<Suspense fallback={<RouteLoading />}><SystemManagementPage user={user} /></Suspense>} />
            <Route path="workflow/governance/model-config" element={<ModelPage />} />
            <Route path="workflow/governance/users" element={<UsersPage />} />
            <Route path="workflow/governance/canary" element={<Suspense fallback={<RouteLoading />}><CanaryRunsPage /></Suspense>} />
            <Route path="workflow/governance/audit" element={<Suspense fallback={<RouteLoading />}><AuditEventsPage /></Suspense>} />

            <Route path="assets" element={<Navigate to="/workflow/materials/packages" replace />} />
            <Route path="jobs" element={<Navigate to="/workflow/materials/jobs" replace />} />
            <Route path="review" element={<Navigate to="/workflow/review/low-confidence" replace />} />
            <Route path="review/:reviewStage" element={<LegacyReviewRedirect />} />
            <Route path="prompts" element={<Navigate to="/workflow/optimization/candidates" replace />} />
            <Route path="model" element={<Navigate to="/workflow/governance/model-config" replace />} />
            <Route path="sample-sets" element={<Navigate to="/legacy/sample-sets" replace />} />
            <Route path="historical-corrections" element={<Navigate to="/legacy/historical-corrections" replace />} />
            <Route path="migrations" element={<Navigate to="/workflow/models/migration" replace />} />
            <Route path="canary-runs" element={<Navigate to="/workflow/governance/canary" replace />} />
            <Route path="legacy/review/:reviewStage" element={<Suspense fallback={<RouteLoading />}><ReviewPage user={user} /></Suspense>} />
            <Route path="legacy/sample-sets" element={<Suspense fallback={<RouteLoading />}><SampleSetsPage /></Suspense>} />
            <Route path="legacy/historical-corrections" element={<Suspense fallback={<RouteLoading />}><HistoricalCorrectionsPage /></Suspense>} />
          </Route>
        ) : (
          <Route path="*" element={<Navigate to="/login" replace />} />
        )}
        <Route path="*" element={<Navigate to={unauthenticated ? "/login" : "/"} replace />} />
      </Routes>
      <Toaster position="top-right" richColors closeButton />
    </>
  )
}

function LegacyReviewRedirect() {
  const { reviewStage } = useParams()
  if (reviewStage === "secondary" || reviewStage === "arbitration") {
    return <Navigate to={`/legacy/review/${reviewStage}`} replace />
  }
  return <Navigate to={`/workflow/review/${reviewStage === "completed" ? "completed" : "low-confidence"}`} replace />
}

function RouteLoading() {
  return <div className="mx-auto max-w-[1540px] px-5 py-10 md:px-8 lg:px-10"><div className="h-64 animate-pulse border-y border-[var(--line)] bg-white" /></div>
}
