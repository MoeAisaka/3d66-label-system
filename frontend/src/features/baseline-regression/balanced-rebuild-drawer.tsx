import type { ReactNode } from "react"

import { SecondaryDrawer } from "@/components/workspace-page"
import type { BalancedRebuildStrategy, BalancedRebuildSurvey } from "@/lib/types"

export function BalancedRebuildDrawer({
  open,
  onOpenChange,
  children,
  footer,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  children: ReactNode
  footer?: ReactNode
}) {
  return <SecondaryDrawer
    open={open}
    onOpenChange={onOpenChange}
    size="wide"
    title="重建均衡样本"
    description="用当前全部人工评级素材重新抽一份均衡样本，冻结为新的基准集；已有样本与它跑过的回归都不会被改动。"
    footer={footer}
  >{children}</SecondaryDrawer>
}

const STRATEGY_LABELS: Record<BalancedRebuildStrategy, string> = {
  stable_hash: "全局均匀抽样（推荐）",
  newest: "最新素材优先",
  oldest: "最早素材优先",
}

const STRATEGY_NOTES: Record<BalancedRebuildStrategy, string> = {
  stable_hash: "在全部已评级素材里按固定算法均匀抽取，不偏向上传时间的任何一端。",
  newest: "每个等级取 asset_id 最大的若干张，用于只看最近一批标注。",
  oldest: "每个等级取 asset_id 最小的若干张，与现有样本的口径一致。",
}

const inputClass = "h-11 w-full rounded-[4px] border border-[var(--line-strong)] bg-white px-3 text-sm"

export function BalancedRebuildForm({
  survey,
  loading,
  error,
  perLevel,
  strategy,
  seed,
  onPerLevel,
  onStrategy,
  onSeed,
}: {
  survey: BalancedRebuildSurvey | undefined
  loading: boolean
  error: unknown
  perLevel: number
  strategy: BalancedRebuildStrategy
  seed: number
  onPerLevel: (value: number) => void
  onStrategy: (value: BalancedRebuildStrategy) => void
  onSeed: (value: number) => void
}) {
  if (loading) {
    return <p className="text-sm text-[var(--muted)]">正在统计可用的人工评级素材…</p>
  }
  if (error) {
    return <div className="border border-[#d7a09d] bg-[#fff5f4] px-3 py-3 text-xs text-[#8d2924]">
      可用素材统计加载失败，暂时无法判断能抽多少张：{String((error as Error).message ?? error)}
    </div>
  }
  if (!survey) return null

  const current = survey.current_balanced_set
  const overQuota = perLevel > survey.max_per_level
  const levels = Object.keys(survey.selectable_distribution).sort()

  return <div className="space-y-6">
    <section className="space-y-3">
      <div>
        <p className="text-sm font-bold">为什么需要重建</p>
        <p className="mt-1 text-xs leading-5 text-[var(--muted)]">
          均衡样本一旦冻结就不能改动——它已经被历史回归引用，原地改写会让那些回归的比较基准失去意义。
          所以想纳入新标注的素材，只能按新参数重新抽一份、冻结成新的基准集。
        </p>
      </div>
      {current && <div className="border border-[var(--line)] bg-[#fafbf8] px-3 py-3 text-xs leading-5">
        <p className="font-semibold">现有样本：{current.name}</p>
        <p className="text-[var(--muted)]">
          {current.item_count} 张 · 已跑过 {current.run_count} 轮回归 · 冻结于 {new Date(current.created_at).toLocaleDateString("zh-CN")}
          {current.max_asset_id != null && <> · 覆盖到 asset_id {current.max_asset_id}</>}
        </p>
        {current.max_asset_id != null && <p className="mt-1 text-[var(--muted)]">
          它是按 asset_id 升序抽的，所以 asset_id 大于 {current.max_asset_id} 的已评级素材永远进不了这份样本。
        </p>}
      </div>}
      <div className="border border-[var(--line)] px-3 py-3 text-xs leading-5">
        <p className="font-semibold">当前可抽取的素材</p>
        <p className="text-[var(--muted)]">
          共 {survey.candidate_total} 张带人工评级前缀；去掉已删除 {survey.deleted_excluded} 张、
          重复图 {survey.duplicate_sha256_skipped} 张后，每级最多可抽 <strong>{survey.max_per_level}</strong> 张。
        </p>
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 font-data text-[0.68rem] text-[var(--muted)]">
          {levels.map((level) => (
            <span key={level}>{level}：{survey.selectable_distribution[level]}</span>
          ))}
        </div>
      </div>
    </section>

    <section className="space-y-4 border-t border-[var(--line)] pt-6">
      <p className="text-sm font-bold">抽样参数</p>
      <div className="grid gap-4 sm:grid-cols-2">
        <label>
          <span className="mb-2 block text-xs font-semibold">每个等级抽几张</span>
          <input
            type="number"
            className={inputClass}
            min={1}
            max={survey.max_per_level || 1}
            value={perLevel}
            onChange={(event) => onPerLevel(Number(event.target.value))}
          />
          <span className="mt-1 block text-[0.68rem] text-[var(--muted)]">
            L1–L5 各取这么多张，合计 {perLevel * 5} 张。
          </span>
          {overQuota && <span className="mt-1 block text-[0.68rem] text-[#8d2924]">
            超过可抽上限 {survey.max_per_level}，会被拒绝；请调低，或先补标注最少的那个等级。
          </span>}
        </label>
        <label>
          <span className="mb-2 block text-xs font-semibold">抽样方式</span>
          <select
            className={inputClass}
            value={strategy}
            onChange={(event) => onStrategy(event.target.value as BalancedRebuildStrategy)}
          >
            {survey.strategies.map((item) => (
              <option key={item} value={item}>{STRATEGY_LABELS[item] ?? item}</option>
            ))}
          </select>
          <span className="mt-1 block text-[0.68rem] text-[var(--muted)]">
            {STRATEGY_NOTES[strategy]}
          </span>
        </label>
        {/* 种子只对全局均匀抽样有意义：按时间取的两种方式完全由上传顺序决定，
            换种子不会改变结果，所以那时不显示这个输入。 */}
        {strategy === "stable_hash" && <label>
          <span className="mb-2 block text-xs font-semibold">随机种子</span>
          <input
            type="number"
            className={inputClass}
            min={1}
            value={seed}
            onChange={(event) => onSeed(Number(event.target.value))}
          />
          <span className="mt-1 block text-[0.68rem] text-[var(--muted)]">
            同一批素材配同一个种子，抽出来的永远是同一份清单；换种子才会重新洗牌。
          </span>
        </label>}
      </div>
      <p className="text-xs leading-5 text-[var(--muted)]">
        抽样结果只取决于上面这几项，所以重复提交同一组参数不会重复冻结，会直接切换到已有的那份。
        新样本冻结后需要重新跑回归才有指标，历史回归仍然挂在原样本上。
      </p>
    </section>
  </div>
}
