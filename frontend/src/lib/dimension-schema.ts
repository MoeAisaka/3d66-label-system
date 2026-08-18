import type {
  DimensionDefinition,
  DimensionSchemaDefinition,
  DimensionSchemaRegistryItem,
  EvaluationDimensionSchema,
  SampleTruth,
} from "@/lib/types"

type ScoreCap = { cap?: unknown }

const AUTHORING_FAMILY_BY_CATEGORY: Record<string, DimensionSchemaRegistryItem["family_key"]> = {
  space_image: "space",
  material_image: "product",
  pdf_text: "intent",
}

export function authoringFamilyForCategory(categoryKey: string | undefined) {
  return AUTHORING_FAMILY_BY_CATEGORY[categoryKey ?? ""] ?? "common"
}

export function isExecutableAuthoringTemplate(
  definition: DimensionSchemaDefinition | null | undefined,
) {
  return Boolean(
    definition
    && Array.isArray(definition.dimensions)
    && definition.dimensions.length > 0
    && definition.aggregation
    && typeof definition.aggregation === "object"
    && definition.output_contract
    && typeof definition.output_contract === "object",
  )
}

export function dimensionAuthoringTemplateCandidates(
  schemas: DimensionSchemaRegistryItem[],
  selectedSchema: DimensionSchemaRegistryItem | undefined,
  categoryKey: string | undefined,
) {
  const preferredFamily = authoringFamilyForCategory(categoryKey)
  const statusOrder: Record<DimensionSchemaRegistryItem["status"], number> = {
    published: 0,
    candidate: 1,
    draft: 2,
    retired: 3,
  }
  const candidates = schemas
    .filter((schema) => schema.id !== selectedSchema?.id)
    .sort((left, right) => (
      Number(left.family_key !== preferredFamily) - Number(right.family_key !== preferredFamily)
      || Number(left.schema_type === "core") - Number(right.schema_type === "core")
      || statusOrder[left.status] - statusOrder[right.status]
      || left.id - right.id
    ))
  return isExecutableAuthoringTemplate(selectedSchema?.definition)
    ? [selectedSchema!, ...candidates]
    : candidates
}

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
  if (aggregation?.preview_mode === "v3_grade_bridge") {
    return calculateV3GradePreview(definitions, aggregation, grades)
  }
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

function calculateV3GradePreview(
  definitions: DimensionDefinition[],
  aggregation: NonNullable<DimensionSchemaDefinition["aggregation"]>,
  grades: Record<string, number>,
): { score: number; level: string } | null {
  const dimensionMax = Number(aggregation.dimension_max)
  const baseScore = Number(aggregation.base_score)
  const trackCap = Number(aggregation.track_cap)
  const levels = aggregation.level_scale
  if (
    !definitions.length
    || !Number.isFinite(dimensionMax)
    || !Number.isFinite(baseScore)
    || !Number.isFinite(trackCap)
    || !Array.isArray(levels)
  ) {
    return null
  }

  let score = baseScore
  for (const definition of definitions) {
    const grade = grades[definition.key]
    const gradePoints = definition.grade_points
    const minPoints = Number(gradePoints?.["1"])
    const maxPoints = Number(gradePoints?.["5"])
    const points = Number(gradePoints?.[String(grade)])
    const weight = Number(definition.weight)
    if (
      !Number.isInteger(grade)
      || grade < 1
      || grade > 5
      || !Number.isFinite(minPoints)
      || !Number.isFinite(maxPoints)
      || maxPoints <= minPoints
      || !Number.isFinite(points)
      || !Number.isFinite(weight)
    ) {
      return null
    }
    score += ((points - minPoints) / (maxPoints - minPoints)) * weight * dimensionMax
  }

  const digits = Number(aggregation.score_round_digits ?? 0)
  const factor = 10 ** digits
  score = Math.min(trackCap, Math.max(0, Math.round(score * factor) / factor))
  const enabledLevels = levels
    .filter((item) => item.enabled === true && Number.isFinite(Number(item.min_score)))
    .map((item) => ({ level: item.level, minScore: Number(item.min_score) }))
    .filter((item): item is { level: string; minScore: number } => (
      typeof item.level === "string" && /^L[1-5]$/.test(item.level)
    ))
    .sort((left, right) => right.minScore - left.minScore)
  const matched = enabledLevels.find((item) => score >= item.minScore)
  return matched ? { score, level: matched.level } : null
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
