import type {
  DimensionDefinition,
  EvaluationDimensionSchema,
  SampleTruth,
} from "@/lib/types"

type ScoreCap = { cap?: unknown }

export function resolvedDimensionDefinitions(
  schema: EvaluationDimensionSchema | null | undefined,
): DimensionDefinition[] {
  if (schema?.status !== "resolved" || !schema.definition) return []
  const definitions = new Map(
    schema.definition.dimensions.map((dimension) => [
      dimension.key,
      dimension,
    ]),
  )
  return schema.dimension_keys.flatMap((key) => {
    const definition = definitions.get(key)
    return definition ? [definition] : []
  })
}

export function dimensionKeys(
  schema: EvaluationDimensionSchema | null | undefined,
): string[] {
  return resolvedDimensionDefinitions(schema).map(
    (dimension) => dimension.key,
  )
}

export function dimensionLabels(
  schema: EvaluationDimensionSchema | null | undefined,
): Record<string, string> {
  return Object.fromEntries(
    resolvedDimensionDefinitions(schema).map((dimension) => [
      dimension.key,
      dimension.label,
    ]),
  )
}

export function truthDimensionDefinitions(
  truth: SampleTruth,
): DimensionDefinition[] {
  const definition = truth.dimension_schema?.definition
  const keys = definition?.output_contract?.dimension_output_keys
  if (!definition || !Array.isArray(keys)) return []
  const definitions = new Map(
    definition.dimensions.map((dimension) => [
      dimension.key,
      dimension,
    ]),
  )
  return keys.flatMap((key) => {
    const dimension = definitions.get(key)
    return dimension ? [dimension] : []
  })
}

export function calculateDimensionPreview(
  schema: EvaluationDimensionSchema | null | undefined,
  grades: Record<string, number>,
  caps: ScoreCap[] = [],
): { score: number; level: string } | null {
  const definitions = resolvedDimensionDefinitions(schema)
  const aggregation = schema?.definition?.aggregation
  const defaultGradePoints = aggregation?.grade_points
  const thresholds = aggregation?.level_thresholds
  if (
    !definitions.length
    || !defaultGradePoints
    || !thresholds
    || !["L2", "L3", "L4", "L5"].every(
      (key) => Number.isFinite(Number(thresholds[key])),
    )
  ) {
    return null
  }

  let score = 0
  for (const definition of definitions) {
    const grade = grades[definition.key]
    const points = Number(
      definition.grade_points?.[String(grade)]
      ?? defaultGradePoints[String(grade)],
    )
    const weight = Number(definition.weight)
    if (
      !Number.isInteger(grade)
      || grade < 1
      || grade > 5
      || !Number.isFinite(points)
      || !Number.isFinite(weight)
    ) {
      return null
    }
    score += points * weight
  }

  const digits = Number(aggregation?.score_round_digits ?? 2)
  const factor = 10 ** digits
  score = Math.round(score * factor) / factor
  let level = levelForScore(score, thresholds)
  const capLevels = caps
    .map((item) => Number(String(item.cap || "").replace("L", "")))
    .filter((value) => Number.isInteger(value) && value >= 1 && value <= 4)
  if (capLevels.length) {
    const cap = Math.min(...capLevels)
    level = `L${Math.min(Number(level.slice(1)), cap)}`
    score = Math.min(score, Number(thresholds[`L${cap + 1}`]) - 1)
  }
  return { score, level }
}

function levelForScore(
  score: number,
  thresholds: Record<string, number>,
): string {
  if (score < Number(thresholds.L2)) return "L1"
  if (score < Number(thresholds.L3)) return "L2"
  if (score < Number(thresholds.L4)) return "L3"
  if (score < Number(thresholds.L5)) return "L4"
  return "L5"
}
