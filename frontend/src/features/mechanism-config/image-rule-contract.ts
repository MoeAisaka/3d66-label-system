import type { Editable, JsonObject } from "./types"

type Json = JsonObject

export type ImageRuleViewDefaults = {
  dimensionScoreCap: number
  deductionRules: Json[]
  bonusRules: Json[]
}

function isRecord(value: unknown): value is Json {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value)
}

function cloneJson<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

export function imageRuleViewDefaults(dimension: Json): ImageRuleViewDefaults {
  return {
    dimensionScoreCap: typeof dimension.dimension_score_cap === "number"
      ? dimension.dimension_score_cap
      : 100,
    deductionRules: Array.isArray(dimension.deduction_rules)
      ? cloneJson(dimension.deduction_rules)
      : [],
    bonusRules: Array.isArray(dimension.bonus_rules)
      ? cloneJson(dimension.bonus_rules)
      : [],
  }
}

function isRuleDimension(dimension: Json): boolean {
  return Array.isArray(dimension.deduction_rules)
    || "bonus_rules" in dimension
    || "dimension_score_cap" in dimension
}

export function prepareImageRulePayload(draft: Editable): Editable {
  const next = cloneJson(draft)
  for (const config of Object.values(next.subcategory_dimensions ?? {})) {
    if (!isRecord(config)) continue
    for (const groupKey of ["common_group", "specific_group"]) {
      const group = config[groupKey]
      const schema = isRecord(group) ? group.schema_definition : null
      const dimensions = isRecord(schema) ? schema.dimensions : null
      if (!Array.isArray(dimensions)) continue
      for (const dimension of dimensions) {
        if (!isRecord(dimension) || !isRuleDimension(dimension)) continue
        if (typeof dimension.dimension_score_cap !== "number") {
          dimension.dimension_score_cap = 100
        }
        if (!Array.isArray(dimension.bonus_rules)) {
          dimension.bonus_rules = []
        }
      }
    }
  }
  return next
}
