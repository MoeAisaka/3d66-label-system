import type { BaselineLevel, BaselineLevelMetrics } from "@/lib/types"

export type BaselineLevelBucketMetric = {
  key: "recommended" | "regular" | "filtered"
  label: "推荐档" | "常规档" | "过滤档"
  levels: readonly BaselineLevel[]
  truePositive: number
  predicted: number
  expected: number
  precision: number | null
  recall: number | null
}

type BucketDefinition = Pick<
  BaselineLevelBucketMetric,
  "key" | "label" | "levels"
>

const buckets: readonly BucketDefinition[] = [
  { key: "recommended", label: "推荐档", levels: ["L1", "L2"] },
  { key: "regular", label: "常规档", levels: ["L3", "L4"] },
  { key: "filtered", label: "过滤档", levels: ["L5"] },
]

const percentFormatter = new Intl.NumberFormat("zh-CN", {
  style: "percent",
  maximumFractionDigits: 2,
})

export function computeBaselineLevelBucketMetrics(
  metrics: BaselineLevelMetrics,
): BaselineLevelBucketMetric[] {
  return buckets.map((bucket) => {
    const bucketLevels = new Set<BaselineLevel>(bucket.levels)
    let truePositive = 0
    let predicted = 0
    let expected = 0

    for (const expectedLevel of metrics.levels) {
      for (const predictedLevel of metrics.levels) {
        const count = metrics.confusion_matrix[expectedLevel]?.[predictedLevel] ?? 0
        if (bucketLevels.has(predictedLevel)) predicted += count
        if (bucketLevels.has(expectedLevel)) expected += count
        if (bucketLevels.has(expectedLevel) && bucketLevels.has(predictedLevel)) {
          truePositive += count
        }
      }
    }

    return {
      ...bucket,
      truePositive,
      predicted,
      expected,
      precision: predicted ? truePositive / predicted : null,
      recall: expected ? truePositive / expected : null,
    }
  })
}

export function LevelPerformanceSummary({ metrics }: { metrics: BaselineLevelMetrics }) {
  const bucketMetrics = computeBaselineLevelBucketMetrics(metrics)

  return (
    <section className="mt-6 space-y-3" aria-label="等级表现">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs font-semibold text-[var(--muted)]">当前回归任务</p>
          <h3 className="mt-1 font-editorial text-xl font-bold">等级表现</h3>
        </div>
        <p className="text-xs text-[var(--muted)]">三档指标为 L1–L5 五档矩阵的附加聚合，不替代原矩阵</p>
      </div>

      <div className="grid gap-px border-y border-[var(--line)] bg-[var(--line)] sm:grid-cols-2">
        <MetricCard label="精确等级准确率" value={formatPercent(metrics.exact_accuracy)} />
        <MetricCard label="相邻等级准确率" value={formatPercent(metrics.adjacent_accuracy)} />
      </div>

      <div className="grid gap-px border-y border-[var(--line)] bg-[var(--line)] md:grid-cols-3">
        {bucketMetrics.map((bucket) => (
          <div key={bucket.key} className="bg-white px-5 py-4">
            <div className="flex items-baseline justify-between gap-3">
              <p className="text-sm font-bold">{bucket.label}</p>
              <p className="font-data text-xs text-[var(--muted)]">{bucket.levels.join("–")}</p>
            </div>
            <div className="mt-4 grid grid-cols-2 gap-3">
              <BucketValue label="精确率" value={formatPercent(bucket.precision)} />
              <BucketValue label="召回率" value={formatPercent(bucket.recall)} />
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-white px-5 py-4">
      <p className="text-xs font-semibold text-[var(--muted)]">{label}</p>
      <p className="font-data mt-2 text-2xl font-bold">{value}</p>
    </div>
  )
}

function BucketValue({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs font-semibold text-[var(--muted)]">{label}</p>
      <p className="font-data mt-1 text-xl font-bold">{value}</p>
    </div>
  )
}

function formatPercent(value: number | null) {
  return value === null ? "—" : percentFormatter.format(value)
}
