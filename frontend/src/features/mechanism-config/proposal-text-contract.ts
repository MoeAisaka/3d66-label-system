import type { JsonObject } from "./types"

export type ProposalContractPath = readonly (string | number)[]

function cloneJson<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

export function patchProposalContract<T extends JsonObject>(
  contract: T,
  path: ProposalContractPath,
  value: unknown,
): T {
  if (path.length === 0) return cloneJson(value) as T
  const next = cloneJson(contract)
  let cursor: any = next
  path.slice(0, -1).forEach((key, index) => {
    const following = path[index + 1]
    if (cursor[key] == null || typeof cursor[key] !== "object") {
      cursor[key] = typeof following === "number" ? [] : {}
    }
    cursor = cursor[key]
  })
  cursor[path[path.length - 1]] = cloneJson(value)
  return next
}

export function proposalChangedPaths(
  original: unknown,
  next: unknown,
  prefix = "contract",
): string[] {
  if (JSON.stringify(original) === JSON.stringify(next)) return []
  if (
    original == null
    || next == null
    || typeof original !== "object"
    || typeof next !== "object"
    || Array.isArray(original)
    || Array.isArray(next)
  ) {
    return [prefix]
  }
  const left = original as JsonObject
  const right = next as JsonObject
  const keys = [...new Set([...Object.keys(left), ...Object.keys(right)])].sort()
  return keys.flatMap((key) => proposalChangedPaths(left[key], right[key], `${prefix}.${key}`))
}
