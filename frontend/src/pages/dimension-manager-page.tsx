import { useEffect, useMemo, useState } from "react"
import { ArrowRight, LockKey, WarningCircle } from "@phosphor-icons/react"
import { useQuery } from "@tanstack/react-query"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { api } from "@/lib/api"
import type {
  DimensionRoutePolicyRegistryItem,
  DimensionSchemaRegistryItem,
} from "@/lib/types"
import { cn } from "@/lib/utils"

const familyNames: Record<string, string> = {
  common: "通用核心",
  space: "空间",
  product: "单品",
  graphic: "平面",
  intent: "意向",
}

const statusNames: Record<string, string> = {
  draft: "草稿",
  candidate: "候选",
  published: "已发布",
  retired: "已停用",
}

const blockerNames: Record<string, string> = {
  manual_calibration_incomplete: "尚未完成 50～100 张人工校准",
  prompt_contract_missing: "尚未配置单品 B 阶段输出合同",
}

export function DimensionManagerPage() {
  const schemas = useQuery({
    queryKey: ["dimension-schemas"],
    queryFn: () => api<{ items: DimensionSchemaRegistryItem[] }>("/api/dimension-schemas"),
  })
  const policies = useQuery({
    queryKey: ["dimension-route-policies"],
    queryFn: () => api<{ items: DimensionRoutePolicyRegistryItem[] }>("/api/dimension-route-policies"),
  })
  const [selectedSchemaId, setSelectedSchemaId] = useState(0)
  const orderedSchemas = useMemo(
    () => [...(schemas.data?.items ?? [])].sort((left, right) => {
      const statusOrder = { candidate: 0, published: 1, draft: 2, retired: 3 }
      return (statusOrder[left.status] ?? 9) - (statusOrder[right.status] ?? 9)
        || left.family_key.localeCompare(right.family_key)
        || right.version.localeCompare(left.version)
    }),
    [schemas.data?.items],
  )
  useEffect(() => {
    if (orderedSchemas.some((item) => item.id === selectedSchemaId)) return
    const productCandidate = orderedSchemas.find(
      (item) => item.family_key === "product" && item.status === "candidate",
    )
    setSelectedSchemaId(productCandidate?.id ?? orderedSchemas[0]?.id ?? 0)
  }, [orderedSchemas, selectedSchemaId])

  const selectedSummary = orderedSchemas.find((item) => item.id === selectedSchemaId)
  const selectedSchema = useQuery({
    queryKey: ["dimension-schema", selectedSummary?.schema_key, selectedSummary?.version],
    queryFn: () => api<DimensionSchemaRegistryItem>(
      `/api/dimension-schemas/${selectedSummary?.schema_key}/versions/${selectedSummary?.version}`,
    ),
    enabled: Boolean(selectedSummary),
  })
  const routeSummary = policies.data?.items[0]
  const routePolicy = useQuery({
    queryKey: ["dimension-route-policy", routeSummary?.policy_key, routeSummary?.version],
    queryFn: () => api<DimensionRoutePolicyRegistryItem>(
      `/api/dimension-route-policies/${routeSummary?.policy_key}/versions/${routeSummary?.version}`,
    ),
    enabled: Boolean(routeSummary),
  })

  const definition = selectedSchema.data?.definition
  const dimensions = [...(definition?.dimensions ?? [])].sort(
    (left, right) => (left.display_order ?? 999) - (right.display_order ?? 999),
  )
  const releaseGate = definition?.release_gate
  const completed = releaseGate?.completed_calibration_samples ?? 0
  const minimum = releaseGate?.minimum_calibration_samples ?? 0

  return (
    <>
      <PageHeader
        index="03.5"
        title="维度管理器"
        description="像管理提示词一样查看维度规则版本。当前页面只读：空间包继续作为生产对照，单品包仍在人工校准候选阶段。"
      />
      <div className="mx-auto max-w-[1540px] px-5 py-8 md:px-8 lg:px-10">
        <div className="flex items-start gap-3 border-y border-[#d4a53d] bg-[#fff9e9] px-4 py-3 text-sm leading-6 text-[#665016]">
          <WarningCircle className="mt-0.5 shrink-0" size={18} weight="fill" />
          <p>
            P2 试点尚未接入生产 Worker。单品候选包只能用于校准，
            未完成样本门禁和 B 阶段合同前不能发布。
          </p>
        </div>

        <div className="mt-6 grid gap-6 lg:grid-cols-[300px_minmax(0,1fr)]">
          <aside className="border-y border-[var(--line-strong)] bg-white">
            <div className="border-b border-[var(--line)] px-4 py-4">
              <h2 className="font-editorial text-xl font-bold">规则版本</h2>
              <p className="mt-1 text-xs text-[var(--muted)]">
                固定语义、族包与候选版本
              </p>
            </div>
            <div className="divide-y divide-[var(--line)]">
              {schemas.isLoading ? (
                <div className="h-64 animate-pulse bg-[#fafbf8]" />
              ) : schemas.isError ? (
                <p className="px-4 py-8 text-sm text-[#8d2924]">
                  维度规则加载失败：{schemas.error.message}
                </p>
              ) : orderedSchemas.map((schema) => (
                <button
                  key={schema.id}
                  className={cn(
                    "w-full px-4 py-4 text-left transition-colors hover:bg-[#f6f8f3]",
                    schema.id === selectedSchemaId && "bg-[#eef4e8]",
                  )}
                  onClick={() => setSelectedSchemaId(schema.id)}
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-sm font-semibold">{schema.display_name}</span>
                    <Badge tone={schema.status === "published" ? "active" : "warning"}>
                      {statusNames[schema.status]}
                    </Badge>
                  </div>
                  <p className="font-data mt-2 text-xs text-[var(--muted)]">
                    {familyNames[schema.family_key]} · {schema.version}
                  </p>
                </button>
              ))}
            </div>
          </aside>

          <main className="min-w-0 space-y-6">
            <section className="border-y border-[var(--line-strong)] bg-white">
              <div className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--line)] px-5 py-5">
                <div>
                  <p className="text-xs font-semibold text-[var(--muted)]">
                    {selectedSchema.data ? familyNames[selectedSchema.data.family_key] : "规则详情"}
                  </p>
                  <h2 className="font-editorial mt-1 text-2xl font-bold">
                    {selectedSchema.data?.display_name ?? "正在读取规则"}
                  </h2>
                  <p className="font-data mt-2 break-all text-xs text-[var(--muted)]">
                    {selectedSchema.data?.canonical_hash ?? "—"}
                  </p>
                </div>
                {selectedSchema.data && (
                  <Badge tone={selectedSchema.data.status === "published" ? "active" : "warning"}>
                    {statusNames[selectedSchema.data.status]}
                  </Badge>
                )}
              </div>

              {selectedSchema.isLoading ? (
                <div className="h-80 animate-pulse bg-[#fafbf8]" />
              ) : selectedSchema.isError ? (
                <p className="px-5 py-10 text-sm text-[#8d2924]">
                  规则详情加载失败：{selectedSchema.error.message}
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[720px] border-collapse text-left">
                    <thead className="bg-[#fafbf8] text-xs text-[var(--muted)]">
                      <tr>
                        <th className="px-5 py-3 font-semibold">顺序</th>
                        <th className="px-5 py-3 font-semibold">维度</th>
                        <th className="px-5 py-3 font-semibold">层级</th>
                        <th className="px-5 py-3 font-semibold">权重</th>
                        <th className="px-5 py-3 font-semibold">3 级判断</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[var(--line)]">
                      {dimensions.map((dimension, index) => (
                        <tr key={dimension.key}>
                          <td className="font-data px-5 py-4 text-xs">{index + 1}</td>
                          <td className="px-5 py-4">
                            <p className="text-sm font-semibold">{dimension.label}</p>
                            <p className="font-data mt-1 text-xs text-[var(--muted)]">{dimension.key}</p>
                          </td>
                          <td className="px-5 py-4">
                            <Badge>{dimension.key && definition?.core_dimension_keys?.includes(dimension.key) ? "固定核心维" : "族内固定维"}</Badge>
                          </td>
                          <td className="font-data px-5 py-4 text-sm">
                            {typeof dimension.weight === "number" ? `${(dimension.weight * 100).toFixed(0)}%` : "由族包分配"}
                          </td>
                          <td className="max-w-[420px] px-5 py-4 text-xs leading-5 text-[var(--muted)]">
                            {dimension.anchors?.["3"] ?? "当前规则未声明"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            {releaseGate && (
              <section className="border-y border-[var(--line-strong)] bg-white px-5 py-5">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div>
                    <div className="flex items-center gap-2">
                      <LockKey size={19} weight="fill" />
                      <h2 className="font-editorial text-xl font-bold">发布门禁</h2>
                    </div>
                    <p className="mt-2 text-sm text-[var(--muted)]">
                      已完成 {completed} / 最少 {minimum} 张人工校准
                    </p>
                  </div>
                  <Badge tone="warning">发布已锁定</Badge>
                </div>
                <div className="mt-4 h-2 overflow-hidden bg-[#edf0e9]">
                  <div
                    className="h-full bg-primary"
                    style={{ width: `${minimum ? Math.min((completed / minimum) * 100, 100) : 0}%` }}
                  />
                </div>
                <div className="mt-4 divide-y divide-[var(--line)] border-y border-[var(--line)]">
                  {(releaseGate.blocked_reasons ?? []).map((reason) => (
                    <div key={reason} className="flex items-center gap-3 py-3 text-sm">
                      <ArrowRight className="shrink-0 text-[var(--muted)]" />
                      <span>{blockerNames[reason] ?? reason}</span>
                    </div>
                  ))}
                </div>
              </section>
            )}

            <section className="border-y border-[var(--line-strong)] bg-white">
              <div className="border-b border-[var(--line)] px-5 py-5">
                <h2 className="font-editorial text-xl font-bold">素材族路由策略</h2>
                <p className="mt-2 text-sm leading-6 text-[var(--muted)]">
                  A 阶段只提供结构化证据；解析器只能从冻结候选集合中选包。
                  未知族回退固定核心维，不会直接丢失结果。
                </p>
              </div>
              {policies.isLoading || routePolicy.isLoading ? (
                <div className="h-48 animate-pulse bg-[#fafbf8]" />
              ) : policies.isError || routePolicy.isError ? (
                <p className="px-5 py-10 text-sm text-[#8d2924]">路由策略加载失败</p>
              ) : routePolicy.data?.definition ? (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[680px] border-collapse text-left">
                    <thead className="bg-[#fafbf8] text-xs text-[var(--muted)]">
                      <tr>
                        <th className="px-5 py-3 font-semibold">识别素材族</th>
                        <th className="px-5 py-3 font-semibold">当前处理</th>
                        <th className="px-5 py-3 font-semibold">规则版本</th>
                        <th className="px-5 py-3 font-semibold">状态</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[var(--line)]">
                      {Object.entries(routePolicy.data.definition.family_routes).map(([family, route]) => (
                        <tr key={family}>
                          <td className="px-5 py-4 text-sm font-semibold">{familyNames[family]}</td>
                          <td className="px-5 py-4 text-sm">{route.mode === "core_fallback" ? "回退固定核心维并人工复核" : "进入素材族维度包"}</td>
                          <td className="font-data px-5 py-4 text-xs">{route.schema_ref.schema_key} · {route.schema_ref.version}</td>
                          <td className="px-5 py-4"><Badge tone={route.schema_ref.status === "published" ? "active" : "warning"}>{statusNames[route.schema_ref.status] ?? route.schema_ref.status}</Badge></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="px-5 py-10 text-sm text-[var(--muted)]">还没有路由策略候选。</p>
              )}
            </section>
          </main>
        </div>
      </div>
    </>
  )
}
