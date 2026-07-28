import { lazy, Suspense } from "react"
import { useQuery } from "@tanstack/react-query"
import { Navigate, Route, Routes } from "react-router-dom"
import { Toaster } from "sonner"

import { AppShell } from "@/components/app-shell"
import { api, ApiError } from "@/lib/api"
import type { User } from "@/lib/types"
import { AssetsPage } from "@/pages/assets-page"
import { DashboardPage } from "@/pages/dashboard-page"
import { JobsPage } from "@/pages/jobs-page"
import { LoginPage } from "@/pages/login-page"
import { ModelPage } from "@/pages/model-page"
import { MigrationsPage } from "@/pages/migrations-page"
import { PromptsPage } from "@/pages/prompts-page"

const SampleSetsPage = lazy(() =>
  import("@/pages/sample-sets-page").then((module) => ({ default: module.SampleSetsPage })),
)
const ReviewPage = lazy(() =>
  import("@/pages/review-page").then((module) => ({ default: module.ReviewPage })),
)
const CanaryRunsPage = lazy(() =>
  import("@/pages/canary-runs-page").then((module) => ({ default: module.CanaryRunsPage })),
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
  const user = me.data

  return (
    <>
      <Routes>
        <Route path="/login" element={<LoginPage user={user} />} />
        {user ? (
          <Route element={<AppShell user={user} />}>
            <Route index element={<DashboardPage />} />
            <Route path="assets" element={<AssetsPage />} />
            <Route path="jobs" element={<JobsPage />} />
            <Route path="review" element={<Suspense fallback={<RouteLoading />}><ReviewPage /></Suspense>} />
            <Route path="prompts" element={<PromptsPage />} />
            <Route path="model" element={<ModelPage />} />
            <Route path="sample-sets" element={<Suspense fallback={<RouteLoading />}><SampleSetsPage /></Suspense>} />
            <Route path="migrations" element={<MigrationsPage />} />
            <Route path="canary-runs" element={<Suspense fallback={<RouteLoading />}><CanaryRunsPage /></Suspense>} />
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

function RouteLoading() {
  return <div className="mx-auto max-w-[1540px] px-5 py-10 md:px-8 lg:px-10"><div className="h-64 animate-pulse border-y border-[var(--line)] bg-white" /></div>
}
