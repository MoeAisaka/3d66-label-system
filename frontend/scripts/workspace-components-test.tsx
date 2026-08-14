import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { useState } from "react"
import { createRoot } from "react-dom/client"
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom"

import { AppShell } from "../src/components/app-shell"
import { RouteErrorBoundary, RouteErrorState } from "../src/components/route-error-state"
import { ConfirmDialog, SecondaryDrawer } from "../src/components/workspace-page"
import "../src/index.css"

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false },
    mutations: { retry: false },
  },
})

let routeShouldThrow = true

function RouteRenderProbe() {
  if (routeShouldThrow) throw new Error("route render probe")
  return <span data-testid="boundary-recovered">边界已恢复</span>
}

function ContractPage() {
  const location = useLocation()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [confirmCount, setConfirmCount] = useState(0)
  const [retryCount, setRetryCount] = useState(0)

  return (
    <div>
      <button type="button" data-testid="open-drawer" onClick={() => setDrawerOpen(true)}>
        打开详情
      </button>
      <button type="button" data-testid="open-confirm" onClick={() => setConfirmOpen(true)}>
        打开确认
      </button>
      <output data-testid="confirm-count">{confirmCount}</output>
      <output data-testid="route-path">{location.pathname}</output>

      <SecondaryDrawer
        open={drawerOpen}
        onOpenChange={setDrawerOpen}
        title="机制详情"
        description="只展示当前操作需要的信息"
        footer={<span>抽屉页脚</span>}
      >
        <span>抽屉正文</span>
      </SecondaryDrawer>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="启用候选版本"
        description="不会自动发布标签事实"
        confirmLabel="确认启用"
        onConfirm={() => {
          setConfirmCount((count) => count + 1)
          setConfirmOpen(false)
        }}
      />

      <RouteErrorState
        title="机制无法读取"
        message="请重试或返回安全页面"
        onRetry={() => setRetryCount((count) => count + 1)}
        backTo="/safe"
      />
      <output data-testid="retry-count">{retryCount}</output>

      <RouteErrorBoundary
        title="页面渲染失败"
        message="已显示安全错误态"
        onRetry={() => {
          routeShouldThrow = false
        }}
      >
        <RouteRenderProbe />
      </RouteErrorBoundary>
    </div>
  )
}

function Harness() {
  return (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/workflow/governance"]}>
        <Routes>
          <Route
            element={
              <AppShell
                user={{
                  id: 1,
                  username: "reviewer",
                  display_name: "评测员",
                  is_admin: true,
                }}
              />
            }
          >
            <Route path="*" element={<ContractPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

function fail(message: string): never {
  document.body.dataset.testStatus = "failed"
  document.body.dataset.testMessage = message
  throw new Error(message)
}

function expectText(text: string) {
  if (!document.body.textContent?.includes(text)) fail(`缺少可见文本：${text}`)
}

function findButton(name: string) {
  const button = Array.from(document.querySelectorAll<HTMLButtonElement>("button")).find(
    (candidate) => candidate.textContent?.trim() === name,
  )
  if (!button) fail(`缺少按钮：${name}`)
  return button
}

createRoot(document.getElementById("root")!).render(<Harness />)

setTimeout(() => {
  expectText("TPENG 标签实验台")
  expectText("LabelLab")

  const drawerTrigger = document.querySelector<HTMLButtonElement>('[data-testid="open-drawer"]')
  if (!drawerTrigger) fail("缺少抽屉入口")
  drawerTrigger.focus()
  drawerTrigger.click()

  setTimeout(() => {
    expectText("机制详情")
    expectText("只展示当前操作需要的信息")
    expectText("抽屉正文")
    expectText("抽屉页脚")

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }))
    setTimeout(() => {
      if (document.body.textContent?.includes("抽屉正文")) fail("按 Escape 后抽屉未关闭")
      if (document.activeElement !== drawerTrigger) fail("关闭抽屉后焦点未返回入口")

      const confirmTrigger = document.querySelector<HTMLButtonElement>('[data-testid="open-confirm"]')
      if (!confirmTrigger) fail("缺少确认入口")
      confirmTrigger.click()

      setTimeout(() => {
        expectText("启用候选版本")
        expectText("不会自动发布标签事实")
        findButton("确认启用").click()

        setTimeout(() => {
          const confirmCount = document.querySelector<HTMLOutputElement>('[data-testid="confirm-count"]')
          if (confirmCount?.textContent !== "1") fail("确认操作未触发")
          if (document.body.textContent?.includes("启用候选版本")) fail("确认后对话框未关闭")

          findButton("重新加载").click()
          setTimeout(() => {
            const retryCount = document.querySelector<HTMLOutputElement>('[data-testid="retry-count"]')
            if (retryCount?.textContent !== "1") fail("错误态重试操作未触发")

            const boundaryHeading = Array.from(document.querySelectorAll("h1")).find(
              (heading) => heading.textContent === "页面渲染失败",
            )
            const boundaryPanel = boundaryHeading?.parentElement
            const boundaryRetry = boundaryPanel?.querySelector<HTMLButtonElement>("button")
            if (!boundaryRetry) fail("错误边界未显示可恢复错误态")
            boundaryRetry.click()

            setTimeout(() => {
              if (!document.querySelector('[data-testid="boundary-recovered"]')) {
                fail("错误边界重试后未恢复子页面")
              }

              findButton("返回").click()
              setTimeout(() => {
                const routePath = document.querySelector<HTMLOutputElement>('[data-testid="route-path"]')
                if (routePath?.textContent !== "/safe") fail("错误态返回路径不正确")
                document.body.dataset.testStatus = "passed"
              }, 80)
            }, 80)
          }, 80)
        }, 80)
      }, 80)
    }, 80)
  }, 80)
}, 120)
