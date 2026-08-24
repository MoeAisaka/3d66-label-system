import type { BaselineLevel, PromptVersion } from "@/lib/types"
import { levels } from "@/features/baseline-regression/regression-page-shared"

export const levelNames: Record<BaselineLevel, string> = {
  L1: "好",
  L2: "中等",
  L3: "中差",
  L4: "极差",
  L5: "过滤",
}

export function LevelSelect({
  label,
  value,
  onChange,
}: {
  label: string
  value: BaselineLevel
  onChange: (value: BaselineLevel) => void
}) {
  return (
    <label>
      <span className="mb-2 block text-xs font-semibold">{label}</span>
      <select
        className="h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm"
        value={value}
        onChange={(event) => onChange(event.target.value as BaselineLevel)}
      >
        {levels.map((level) => (
          <option key={level} value={level}>{level} · {levelNames[level]}</option>
        ))}
      </select>
    </label>
  )
}

export function PromptSelect({
  label,
  value,
  options,
  published,
  disabled,
  onChange,
}: {
  label: string
  value: number
  options: PromptVersion[]
  published: PromptVersion | undefined
  disabled: boolean
  onChange: (value: number) => void
}) {
  return (
    <label>
      <span className="mb-2 block text-xs font-semibold">{label}</span>
      <select
        className="h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm disabled:border-[var(--line)] disabled:bg-[#f1f3ef] disabled:text-[var(--muted)]"
        value={disabled ? (published?.id ?? "") : (value || "")}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
      >
        {!published && disabled && (
          <option value="">当前发布版本未配置</option>
        )}
        {!options.length && !disabled && (
          <option value="">暂无可选版本</option>
        )}
        {options.map((prompt) => (
          <option key={prompt.id} value={prompt.id}>
            {prompt.version} · {prompt.name} · {promptStatusName(prompt.status)} · {promptScopeName(prompt.pipeline_scope)}
          </option>
        ))}
      </select>
    </label>
  )
}

export function promptStatusName(status: PromptVersion["status"]) {
  if (status === "published") return "已发布"
  if (status === "archived") return "已归档"
  return "草稿"
}

export function promptScopeName(scope: PromptVersion["pipeline_scope"]) {
  if (scope === "baseline_regression") return "基准回归专用"
  if (scope === "full_pipeline") return "完整流水线专用"
  return "共用"
}
