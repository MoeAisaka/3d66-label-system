import type { Editable, JsonObject } from "./types"

type Json = JsonObject

export type ImageRuleViewDefaults = {
  dimensionScoreCap: number
  dimensionDeductionCap: number
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
    dimensionDeductionCap: typeof dimension.dimension_deduction_cap === "number"
      ? dimension.dimension_deduction_cap
      : 100,
    deductionRules: Array.isArray(dimension.deduction_rules)
      ? cloneJson(dimension.deduction_rules)
      : [],
    bonusRules: Array.isArray(dimension.bonus_rules)
      ? cloneJson(dimension.bonus_rules)
      : [],
  }
}

export type ImageRuleBindingView = {
  callAVersion: string
  callBVersion: string
  foundationEnabled: boolean
}

export function imageRuleBindingView(contract: Json | null | undefined): ImageRuleBindingView {
  const source = isRecord(contract) ? contract : {}
  const bindings = isRecord(source.prompt_bindings) ? source.prompt_bindings : {}
  return {
    callAVersion: typeof bindings.call_a_version === "string" ? bindings.call_a_version : "",
    callBVersion: typeof bindings.call_b_version === "string" ? bindings.call_b_version : "",
    foundationEnabled: isRecord(source.aesthetic_foundation),
  }
}

/**
 * 写入运营手选的 A/B 绑定。
 *
 * 美感前置基座自己也声明一份 call_b_version，后端门禁要求两处相等，不等就以
 * aesthetic_foundation_prompt_binding_mismatch 拒单。所以改 B 必须一并改基座，
 * 否则运营在界面上存出来的修订一定跑不起来。
 */
export function applyImageRuleBinding(
  contract: Json,
  stage: "A" | "B",
  version: string,
): void {
  const bindings = isRecord(contract.prompt_bindings) ? contract.prompt_bindings : {}
  contract.prompt_bindings = bindings
  // 「未绑定」的规范值是 null，不是空串：后端 call_b_version 允许 None 表示这条
  // 修订不走调用 B，空串会变成一个声明了却对不上任何版本的假绑定。
  const trimmed = version.trim()
  const next = trimmed === "" ? null : trimmed
  if (stage === "A") {
    bindings.call_a_version = next
    return
  }
  bindings.call_b_version = next
  const foundation = contract.aesthetic_foundation
  if (isRecord(foundation)) {
    foundation.call_b_version = next
  }
}

/**
 * 开关美感前置基座（锚图赛道）。
 *
 * 关掉就是从合同里删掉整个 aesthetic_foundation——worker 侧正是以「合同里有没有
 * 这个块」判断锚图赛道是否激活的。重新开启只能从原修订恢复：基座里的锚图资产、
 * 维度键、分档切点都是标定过的内容，界面凭空造不出来，没有模板时如实拒绝。
 */
export function setAestheticFoundationEnabled(
  contract: Json,
  enabled: boolean,
  template: Json | null | undefined,
): boolean {
  if (!enabled) {
    delete contract.aesthetic_foundation
    return true
  }
  if (isRecord(contract.aesthetic_foundation)) return true
  if (!isRecord(template)) return false
  const restored = cloneJson(template)
  const bindings = isRecord(contract.prompt_bindings) ? contract.prompt_bindings : {}
  // 一律对齐当前绑定（含未绑定的 null），否则模板里的旧版本号会留下来，
  // 直接撞上 aesthetic_foundation_prompt_binding_mismatch。
  const bound = bindings.call_b_version
  restored.call_b_version = typeof bound === "string" ? bound : null
  contract.aesthetic_foundation = restored
  return true
}

function isRuleDimension(dimension: Json): boolean {
  return Array.isArray(dimension.deduction_rules)
    || "bonus_rules" in dimension
    || "dimension_score_cap" in dimension
    || "dimension_deduction_cap" in dimension
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
        if (typeof dimension.dimension_deduction_cap !== "number") {
          dimension.dimension_deduction_cap = 100
        }
        if (!Array.isArray(dimension.bonus_rules)) {
          dimension.bonus_rules = []
        }
      }
    }
  }
  return next
}
