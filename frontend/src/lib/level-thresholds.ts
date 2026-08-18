export type LevelThresholds = Record<string, number>

export const dimensionGradeOptions = [
  { grade: 5, label: "优秀" },
  { grade: 4, label: "较好" },
  { grade: 3, label: "中等" },
  { grade: 2, label: "较差" },
  { grade: 1, label: "很差" },
] as const

export function levelForMinimumScore(
  score: number,
  thresholds: LevelThresholds,
): string {
  const ordered = Object.entries(thresholds)
    .filter(([level, minimum]) => /^L[1-5]$/.test(level) && Number.isFinite(Number(minimum)))
    .sort((left, right) => Number(right[1]) - Number(left[1]))
  for (const [level, minimum] of ordered) {
    if (score >= Number(minimum)) return level
  }
  return ordered.at(-1)?.[0] ?? "L5"
}
