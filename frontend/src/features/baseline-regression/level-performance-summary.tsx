import type { BaselineLevel, BaselineLevelMetrics } from "@/lib/types"
import { BASELINE_LEVELS, computeBaselineLevelMatrixMetrics } from "./level-metrics"

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
  const matrixMetrics = computeBaselineLevelMatrixMetrics(metrics)
  // 准确率的分母是有效预测数，不是样本总数。不显示分母时，一个 2/100 有效的
  // 回归会呈现成"准确率 100%"，看不出这个数字只由 2 条样本得出。
  const unscored = metrics.unscored ?? 0
  const basisHint =
    metrics.valid_predictions < metrics.total
      ? `基于 ${metrics.valid_predictions}/${metrics.total} 条有效预测` +
        (unscored ? `，${unscored} 条未评级` : "")
      : `基于全部 ${metrics.total} 条`

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
        <MetricCard
          label="精确等级准确率"
          value={formatPercent(metrics.exact_accuracy)}
          testId="baseline-exact-accuracy"
          hint={basisHint}
        />
        <MetricCard
          label="相邻等级准确率"
          value={formatPercent(metrics.adjacent_accuracy)}
          testId="baseline-adjacent-accuracy"
          hint={basisHint}
        />
      </div>

      <div className="grid gap-px border-y border-[var(--line)] bg-[var(--line)] md:grid-cols-3">
        {bucketMetrics.map((bucket) => (
          <div
            key={bucket.key}
            className="bg-white px-5 py-4"
            data-testid={`baseline-bucket-${bucket.key}`}
          >
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

      <section className="space-y-3" aria-label="L1–L5 等级分布矩阵" data-testid="baseline-level-matrix">
        <div>
          <p className="text-sm font-bold">L1–L5 等级分布矩阵</p>
          <p className="mt-1 text-xs text-[var(--muted)]">行是人工真值，列是模型预测；行末为召回率，底部为单档准确率（Precision）。</p>
        </div>
        <div className="overflow-x-auto border border-[var(--line-strong)] bg-white">
          <div className="min-w-[620px]">
            <div className="grid grid-cols-[88px_repeat(5,minmax(62px,1fr))_96px] border-b border-[var(--line)] bg-[#fafbf8] text-xs font-semibold text-[var(--muted)]">
              <div className="px-3 py-3">真值＼预测</div>
              {BASELINE_LEVELS.map((level) => <div key={`header-${level}`} className="px-3 py-3 text-center">{level}</div>)}
              <div className="px-3 py-3 text-center">召回率</div>
            </div>
            {BASELINE_LEVELS.map((expected) => (
              <div key={`row-${expected}`} className="grid grid-cols-[88px_repeat(5,minmax(62px,1fr))_96px] border-b border-[var(--line)] last:border-0">
                <div className="flex items-center px-3 py-3 text-xs font-semibold">{expected}</div>
                {BASELINE_LEVELS.map((predicted) => {
                  const cell = matrixMetrics.cells.find((item) => item.expected === expected && item.predicted === predicted)
                  const count = cell?.count ?? 0
                  return (
                    <div
                      key={`${expected}-${predicted}`}
                      className={`flex items-center justify-center border-l border-[var(--line)] px-2 py-3 font-data text-sm ${expected === predicted && count > 0 ? "bg-[#f1f8cf] font-bold" : ""}`}
                      data-testid={`baseline-level-cell-${expected}-${predicted}`}
                      aria-label={`${expected} 真值、${predicted} 预测：${count} 条`}
                    >
                      {count}
                    </div>
                  )
                })}
                <div className="flex items-center justify-center border-l border-[var(--line)] px-2 py-3 font-data text-sm" data-testid={`baseline-level-recall-${expected}`}>
                  {formatPercent(matrixMetrics.recallByLevel[expected])}
                </div>
              </div>
            ))}
            <div className="grid grid-cols-[88px_repeat(5,minmax(62px,1fr))_96px] bg-[#fafbf8] text-xs">
              <div className="px-3 py-3 font-semibold">准确率（Precision）</div>
              {BASELINE_LEVELS.map((level) => <div key={`precision-${level}`} className="border-l border-[var(--line)] px-2 py-3 text-center font-data" data-testid={`baseline-level-precision-${level}`}>{formatPercent(matrixMetrics.precisionByLevel[level])}</div>)}
              <div className="border-l border-[var(--line)] px-2 py-3 text-center text-[var(--muted)]">—</div>
            </div>
          </div>
        </div>
      </section>
    </section>
  )
}

function MetricCard({
  label,
  value,
  testId,
  hint,
}: {
  label: string
  value: string
  testId: string
  hint?: string
}) {
  return (
    <div className="bg-white px-5 py-4">
      <p className="text-xs font-semibold text-[var(--muted)]">{label}</p>
      <p className="font-data mt-2 text-2xl font-bold" data-testid={testId}>{value}</p>
      {hint ? (
        <p className="font-data mt-1 text-xs text-[var(--muted)]">{hint}</p>
      ) : null}
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
