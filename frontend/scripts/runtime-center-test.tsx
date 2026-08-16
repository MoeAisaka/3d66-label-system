import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { createRoot } from "react-dom/client"
import { MemoryRouter } from "react-router-dom"

import { OperationsCenterPage } from "../src/pages/operations-center-page"
import "../src/index.css"

const run = {
  id: 1,
  run_key: "run-browser-fixture",
  idempotency_key: "browser-fixture",
  source_type: "category_evaluation",
  source_id: 42,
  source_run_id: null,
  workflow_definition_id: 1,
  workflow_version_id: 1,
  workflow_version: "1",
  snapshot_hash: "a".repeat(64),
  category_key: "model_3d_su",
  queue_class: "validation",
  status: "queued",
  current_step_key: "identity",
  blockers: [],
  requested_by: "browser",
  owner: "platform-owner",
  reason: "Edge browser acceptance",
  environment: "dry_run",
  total_steps: 2,
  completed_steps: 0,
  failed_steps: 0,
  last_checkpoint_id: null,
  attempt_count: 0,
  next_retry_at: null,
  error_code: "",
  error_message: "",
  created_at: "2026-08-15T08:00:00Z",
  updated_at: "2026-08-15T08:00:00Z",
  started_at: null,
  finished_at: null,
  allowed_actions: ["cancel", "pause"],
}

const queueClasses = ["validation", "interactive", "production_batch", "canary", "recovery"]

window.fetch = async (input) => {
  const url = String(input)
  let body: unknown
  if (url.startsWith("/api/jobs/control")) {
    body = { paused: false, queued_count: 1, processing_count: 0, paused_count: 0, active_count: 1, updated_at: "2026-08-15T08:00:00Z" }
  } else if (url.startsWith("/api/jobs?")) {
    body = { items: [] }
  } else if (url.startsWith("/api/queues/status")) {
    body = {
      version: "queue-policy-v1",
      global_limit: 5,
      shares: {},
      weights: {},
      validation_boost: 10,
      credentials_configured: true,
      control_paused: false,
      queues: queueClasses.map((queue_class, index) => ({
        queue_class,
        pending: index === 0 ? 1 : 0,
        pending_total: index === 0 ? 1 : 0,
        running: 0,
        reserved: 1,
        borrowed: 0,
        effective_limit: 1,
        weight: 1,
        effective_weight: 1,
        blocked_by_breaker: 0,
        blocked_by_credentials: 0,
        blocked_by_control: 0,
        delayed_by_retry_after: 0,
        dispatchable_pending: index === 0 ? 1 : 0,
      })),
    }
  } else if (url.startsWith("/api/circuit-breakers")) {
    body = { items: [] }
  } else if (url.endsWith("/timeline")) {
    body = {
      run_key: run.run_key,
      items: [{
        id: 1,
        step_key: "identity",
        step_type: "identity",
        sequence: 0,
        attempt_no: 1,
        status: "pending",
        script_version_id: 1,
        script_version: "1",
        queue_class: "validation",
        input_hash: "b".repeat(64),
        output_hash: null,
        checkpoint_hash: null,
        lease_owner: null,
        lease_expires_at: null,
        last_error_code: "",
        last_error_message: "",
        started_at: null,
        finished_at: null,
      }],
    }
  } else if (url.endsWith("/snapshot")) {
    body = {
      run_key: run.run_key,
      snapshot_hash: run.snapshot_hash,
      snapshot: {
        schema_version: "production-run-snapshot-v1",
        workflow: { version: "1", canonical_hash: "c".repeat(64) },
        scripts: [{ script_key: "fixture.identity", version: "1", artifact_sha256: "d".repeat(64) }],
        runtime_context: { environment: "dry_run", category_key: "model_3d_su" },
      },
    }
  } else if (url === "/api/runtime/runs/run-browser-fixture") {
    body = run
  } else if (url.startsWith("/api/runtime/runs")) {
    body = { items: [run] }
  } else {
    return new Response(JSON.stringify({ detail: "unhandled fixture URL: " + url }), { status: 404 })
  }
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })
}

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
})

createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={queryClient}>
    <MemoryRouter>
      <OperationsCenterPage />
    </MemoryRouter>
  </QueryClientProvider>,
)

setTimeout(() => {
  const text = document.body.textContent ?? ""
  const ready = ["通用工作流运行时", "当前步骤", "最后检查点", "责任人", "阻塞原因"].every((item) => text.includes(item))
  document.body.dataset.testStatus = ready ? "ready" : "failed"
}, 800)
