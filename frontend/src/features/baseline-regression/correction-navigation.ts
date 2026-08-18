export type CorrectionNavigationItem = {
  id: number
  review_stage?: string | null
}

/** Return the next item after current that still needs an initial decision. */
export function nextPendingCorrectionId(
  items: readonly CorrectionNavigationItem[],
  currentId: number,
): number | null {
  const currentIndex = items.findIndex((item) => item.id === currentId)
  const candidates = currentIndex >= 0 ? items.slice(currentIndex + 1) : items
  return candidates.find((item) => item.review_stage !== "completed")?.id ?? null
}

/** Return the immediately preceding item in the current correction list. */
export function previousCorrectionId(
  items: readonly CorrectionNavigationItem[],
  currentId: number,
): number | null {
  const currentIndex = items.findIndex((item) => item.id === currentId)
  if (currentIndex <= 0) return null
  return items[currentIndex - 1]?.id ?? null
}
