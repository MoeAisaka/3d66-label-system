import { SecondaryDrawer } from "@/components/workspace-page"
import type { BaselineSemanticQualityMetrics } from "@/lib/types"

export function SemanticQualityDrawer({
  open,
  onOpenChange,
  data,
  loading,
  error,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  data?: BaselineSemanticQualityMetrics
  loading: boolean
  error: Error | null
}) {
  return <SecondaryDrawer open={open} onOpenChange={onOpenChange} size="wide" title="语义字段质量" description="按实体 ID 统计 Precision、Recall、映射覆盖、冲突和人工纠偏证据；不替代原有 L1–L5 等级矩阵。">
    {loading ? <div className="h-52 animate-pulse bg-[#f7f9ef]" /> : error ? <div className="border border-[#d7a09d] bg-[#fff5f4] px-4 py-4 text-sm text-[#8d2924]">语义字段质量加载失败：{error.message}</div> : !data ? <p className="text-sm text-[var(--muted)]">当前轮次尚未形成语义字段质量证据。</p> : <div className="space-y-6">
      <section className="grid grid-cols-2 gap-px border-y border-[var(--line)] bg-[var(--line)] sm:grid-cols-4"><Metric label="宏 Precision" value={formatRate(data.aggregates.macro_precision)} /><Metric label="宏 Recall" value={formatRate(data.aggregates.macro_recall)} /><Metric label="微 Precision" value={formatRate(data.aggregates.micro_precision)} /><Metric label="微 Recall" value={formatRate(data.aggregates.micro_recall)} /></section>
      <section className="overflow-x-auto border-y border-[var(--line-strong)] bg-white">
        <table className="w-full min-w-[1120px] border-collapse text-left text-xs">
          <thead><tr className="border-b border-[var(--line)] bg-[#fafbf8]"><th className="px-3 py-3">字段</th><th className="px-3 py-3">Precision</th><th className="px-3 py-3">Recall</th><th className="px-3 py-3">映射覆盖</th><th className="px-3 py-3">未映射率</th><th className="px-3 py-3">冲突率</th><th className="px-3 py-3">空值语义</th><th className="px-3 py-3">纠偏率</th><th className="px-3 py-3">审核覆盖</th></tr></thead>
          <tbody>{Object.values(data.fields).map((field) => <tr key={field.field_key} className="border-b border-[var(--line)] last:border-0"><td className="font-data px-3 py-3 font-semibold">{field.field_key}</td><td className="font-data px-3 py-3">{formatRate(field.precision)}</td><td className="font-data px-3 py-3">{formatRate(field.recall)}</td><td className="font-data px-3 py-3">{formatRate(field.mapping_coverage)}</td><td className="font-data px-3 py-3">{formatRate(field.unmapped_rate)}</td><td className="font-data px-3 py-3">{formatRate(field.conflict_rate)}</td><td className="font-data px-3 py-3">{formatRate(field.null_semantics_accuracy)}</td><td className="font-data px-3 py-3">{formatRate(field.correction_rate)}</td><td className="font-data px-3 py-3">{formatRate(field.review_coverage)}</td></tr>)}</tbody>
        </table>
      </section>
      <p className="border-l-2 border-primary bg-[#f7fadf] px-4 py-3 text-xs leading-5">零分母显示“—”。该证据与精确等级准确率、相邻等级准确率、推荐档、常规档、过滤档及完整五档矩阵并行展示。</p>
    </div>}
  </SecondaryDrawer>
}

function Metric({ label, value }: { label: string; value: string }) { return <div className="bg-white p-4"><p className="text-xs text-[var(--muted)]">{label}</p><p className="font-data mt-2 text-xl font-bold">{value}</p></div> }
function formatRate(value: number | null | undefined) { return value == null ? "—" : `${(value * 100).toFixed(1)}%` }
