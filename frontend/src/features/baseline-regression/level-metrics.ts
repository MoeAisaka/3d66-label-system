import type { BaselineLevel, BaselineLevelMetrics } from "../../lib/types"

export const BASELINE_LEVELS: readonly BaselineLevel[] = ["L1", "L2", "L3", "L4", "L5"]

export type BaselineLevelMatrixMetrics = {
  levels: readonly BaselineLevel[]
  rowTotals: Record<BaselineLevel, number>
  columnTotals: Record<BaselineLevel, number>
  recallByLevel: Record<BaselineLevel, number | null>
  precisionByLevel: Record<BaselineLevel, number | null>
  cells: Array<{ expected: BaselineLevel; predicted: BaselineLevel; count: number }>
}

export function computeBaselineLevelMatrixMetrics(
  metrics: Pick<BaselineLevelMetrics, "confusion_matrix">,
): BaselineLevelMatrixMetrics {
  const rowTotals = emptyLevelRecord()
  const columnTotals = emptyLevelRecord()
  const diagonal = emptyLevelRecord()
  const cells: BaselineLevelMatrixMetrics["cells"] = []

  for (const expected of BASELINE_LEVELS) {
    for (const predicted of BASELINE_LEVELS) {
      const count = metrics.confusion_matrix?.[expected]?.[predicted] ?? 0
      cells.push({ expected, predicted, count })
      rowTotals[expected] += count
      columnTotals[predicted] += count
      if (expected === predicted) diagonal[expected] += count
    }
  }

  const recallByLevel = emptyNullableLevelRecord()
  const precisionByLevel = emptyNullableLevelRecord()
  for (const level of BASELINE_LEVELS) {
    recallByLevel[level] = rowTotals[level] ? diagonal[level] / rowTotals[level] : null
    precisionByLevel[level] = columnTotals[level] ? diagonal[level] / columnTotals[level] : null
  }

  return { levels: BASELINE_LEVELS, rowTotals, columnTotals, recallByLevel, precisionByLevel, cells }
}

function emptyLevelRecord(): Record<BaselineLevel, number> {
  return { L1: 0, L2: 0, L3: 0, L4: 0, L5: 0 }
}

function emptyNullableLevelRecord(): Record<BaselineLevel, number | null> {
  return { L1: null, L2: null, L3: null, L4: null, L5: null }
}
