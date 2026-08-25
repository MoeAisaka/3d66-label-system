import type { BaselineFieldMetrics, BaselineRegressionRun } from "@/lib/types"
import { Metric, percent } from "@/features/baseline-regression/regression-page-shared"

export function FieldMetricsEvidence({
  data,
  loading,
  error,
  levelMetrics,
}: {
  data?: BaselineFieldMetrics
  loading: boolean
  error: Error | null
  levelMetrics: BaselineRegressionRun["metrics"]
}) {
  if (loading) {
    return <div className="h-52 animate-pulse bg-[#f7f9ef]" />
  }
  if (error) {
    return (
      <div className="border border-[#d7a09d] bg-[#fff5f4] px-4 py-4 text-sm text-[#8d2924]">
        字段质量证据加载失败：{error.message}
      </div>
    )
  }
  if (!data) {
    return <p className="text-sm text-[var(--muted)]">当前轮次尚未形成字段证据。</p>
  }

  return (
    <div className="space-y-6">
      <section className="grid gap-px border-y border-[var(--line)] bg-[var(--line)] grid-cols-2 xl:grid-cols-4">
        <Metric label="宏平均准确率" value={percent(data.aggregates.macro.accuracy)} />
        <Metric label="宏平均召回率" value={percent(data.aggregates.macro.recall)} />
        <Metric label="微平均准确率" value={percent(data.aggregates.micro.accuracy)} />
        <Metric label="失败样本" value={String(data.failure_sample_ids.length)} />
      </section>

      <section className="border-y border-[var(--line-strong)] bg-white">
        <div className="grid grid-cols-[minmax(210px,1fr)_90px_110px_110px_100px] gap-3 border-b border-[var(--line)] bg-[#fafbf8] px-4 py-3 text-xs font-semibold text-[var(--muted)]">
          <span>字段</span><span>支持数</span><span>准确率</span><span>召回率</span><span>失败数</span>
        </div>
        {data.field_metrics.map((item) => (
          <details
            key={item.field_key}
            className="border-b border-[var(--line)] last:border-0"
            data-testid={`baseline-field-metric-${item.field_key}`}
          >
            <summary className="grid cursor-pointer grid-cols-[minmax(210px,1fr)_90px_110px_110px_100px] gap-3 px-4 py-3 text-sm hover:bg-[#fbfcf5]">
              <span className="font-data break-all font-semibold">{fieldMetricLabel(item.field_key)}</span>
              <span className="font-data">{item.support}</span>
              <span className="font-data">{percent(item.accuracy)}</span>
              <span className="font-data">{percent(item.recall)}</span>
              <span className="font-data">{item.failure_sample_ids.length}</span>
            </summary>
            <div className="space-y-4 border-t border-[var(--line)] bg-[#fcfdf8] px-4 py-4">
              <div className="grid gap-3 grid-cols-3">
                <EvidenceMetric label="TP" value={item.tp} />
                <EvidenceMetric label="FP" value={item.fp} />
                <EvidenceMetric label="FN" value={item.fn} />
              </div>
              <div
                className="overflow-x-auto border border-[var(--line)] bg-white"
                data-testid={item.field_key === "level" ? "baseline-five-level-confusion-matrix" : undefined}
              >
                <table className="w-full min-w-[520px] border-collapse text-left text-xs">
                  <thead><tr className="border-b border-[var(--line)] bg-[#fafbf8]"><th className="px-3 py-2">人工真值</th><th className="px-3 py-2">模型输出</th><th className="px-3 py-2">样本数</th></tr></thead>
                  <tbody>
                    {Object.entries(item.confusion_matrix).flatMap(([expected, predictions]) => (
                      Object.entries(predictions).map(([predicted, count]) => (
                        <tr key={`${expected}:${predicted}`} className="border-b border-[var(--line)] last:border-0">
                          <td className="font-data px-3 py-2">{fieldMetricValue(expected)}</td>
                          <td className="font-data px-3 py-2">{fieldMetricValue(predicted)}</td>
                          <td className="font-data px-3 py-2">{count}</td>
                        </tr>
                      ))
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-xs leading-5 text-[var(--muted)]">
                失败样本：{item.failure_sample_ids.length ? item.failure_sample_ids.map((id) => `#${id}`).join("、") : "无"}
              </p>
            </div>
          </details>
        ))}
      </section>

      <section className="grid gap-4 border-y border-[var(--line)] bg-[#f7f9ef] px-4 py-4 text-xs leading-5 grid-cols-2">
        <div>
          <p className="font-semibold">本轮版本</p>
          <p className="mt-1 text-[var(--muted)]">模型 {data.versions.model.join(" / ") || "未记录"}</p>
          <p className="text-[var(--muted)]">Prompt A {data.versions.prompt.a.join(" / ") || "未记录"}</p>
          <p className="text-[var(--muted)]">Prompt B {data.versions.prompt.b.join(" / ") || "未使用"}</p>
          <p className="text-[var(--muted)]">机制 {data.versions.mechanism.spec_version || "历史版本未记录"}</p>
        </div>
        <div>
          <p className="font-semibold">证据覆盖</p>
          <p className="mt-1 text-[var(--muted)]">素材 {data.versions.asset.count} 条 · 黄金真值匹配 {data.versions.truth.matched_asset_count} 条</p>
          <p className="text-[var(--muted)]">真值修订 V{data.versions.truth.revision_min}–V{data.versions.truth.revision_max}</p>
          <p className="text-[var(--muted)]">等级准确率 {percent(levelMetrics.exact_accuracy)} · 等级矩阵继续作为字段 level 的详细证据</p>
        </div>
      </section>

      <div className="border-l-2 border-primary bg-[#f7fadf] px-4 py-3 text-xs leading-5">
        指标仅作为人工决策证据，不会自动采纳或启用候选评测机制。人工需结合失败样本、黄金集门禁和回归结果另行决定。
      </div>
    </div>
  )
}

export function EvidenceMetric({ label, value }: { label: string; value: number }) {
  return <div className="border border-[var(--line)] bg-white px-3 py-3"><p className="text-[var(--muted)]">{label}</p><p className="font-data mt-1 text-lg font-bold">{value}</p></div>
}

export function fieldMetricLabel(fieldKey: string) {
  const labels: Record<string, string> = {
    level: "正式等级",
    scope_status: "范围判定",
    primary_category: "主类目",
    quality_severity: "画质严重度",
  }
  if (fieldKey.startsWith("dimensions.")) return `维度 · ${fieldKey.slice("dimensions.".length)}`
  return labels[fieldKey] ?? fieldKey
}

export function fieldMetricValue(value: string) {
  if (value === "__missing__") return "缺失"
  if (value === "__empty__") return "空值"
  return value
}
