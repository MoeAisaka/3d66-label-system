/** 等级刻度模型 —— 从 image-rule-editor.tsx 抽出的纯数据推导,不含 JSX。 */

import type { JsonObject } from "./types"

type Json = JsonObject

export type LevelScaleEntry = {
  level: "L1" | "L2" | "L3" | "L4" | "L5"
  enabled: boolean
  min_score?: number
  display_name: string
}

export const LEVELS: LevelScaleEntry["level"][] = ["L1", "L2", "L3", "L4", "L5"]
export function levelScaleForEditor(contract: Json): LevelScaleEntry[] {
  const configured = contract?.level_scale?.levels
  if (Array.isArray(configured)) {
    return LEVELS.map((level) => {
      const entry = configured.find((item: any) => item?.level === level)
      return {
        level,
        enabled: entry?.enabled !== false,
        min_score: typeof entry?.min_score === "number" ? entry.min_score : undefined,
        display_name: typeof entry?.display_name === "string" ? entry.display_name : level,
      }
    })
  }
  const thresholds = Array.isArray(contract?.level_thresholds) ? contract.level_thresholds : []
  return LEVELS.map((level) => {
    const threshold = thresholds.find((item: any) => item?.level === level)
    return {
      level,
      enabled: Boolean(threshold),
      min_score: typeof threshold?.min_score === "number" ? threshold.min_score : undefined,
      display_name: level,
    }
  })
}
