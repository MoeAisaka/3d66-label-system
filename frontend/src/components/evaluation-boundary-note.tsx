import { useState } from "react"
import { CaretDown, Info } from "@phosphor-icons/react"

/**
 * ADR-0033 三位一体边界说明。
 *
 * 评测流程有三个可配置位置，职责严格分离，避免使用者把规则填错位置：
 * - 调用 A（预检/事实识别）：只看“这是什么、有没有硬伤/红线信号”，产出事实字段与红线信号，
 *   不出维度分、不出最终 L 等级。
 * - 调用 B（维度评价）：只看“做得好不好”，按赛道逐条判定扣分规则并给可见证据，
 *   不出总分、不做媒介降权与封顶。
 * - 维度层（服务端确定性）：把 A、B 的结果用纯函数算成分数与 L 等级（红线→赛道→维度扣分→
 *   媒介降权→高分压分→赛道封顶→分数映射），可回归、不调用模型。
 *
 * 传 `slot` 高亮当前配置位置对应的那一条。
 */
export type EvaluationBoundarySlot = "A" | "B" | "dimension"

const ROWS: { slot: EvaluationBoundarySlot; title: string; body: string }[] = [
  {
    slot: "A",
    title: "调用 A · 预检（事实识别）",
    body: "只识别“这是什么、是否命中红线信号、有没有可见硬伤”。产出分类、图片形态、画质、生产字段与红线信号，不打维度分、不给最终 L 等级。",
  },
  {
    slot: "B",
    title: "调用 B · 维度评价（好不好）",
    body: "只按赛道逐条判断维度扣分规则是否命中，并为每条命中规则给出独立证据；不算总分、不做媒介降权与封顶。旧合同没有扣分规则时才兼容 grade（1-5）路径。",
  },
  {
    slot: "dimension",
    title: "维度层 · 服务端确定性算分",
    body: "把 A、B 的结果按固定顺序算成分数与 L 等级：红线门 → 赛道分类 → 维度扣分 → 媒介降权 → 高分一票压分 → 赛道封顶 → 分数映射。纯函数、可回归、不调用模型；红线与降权规则只落在这里，不写进提示词。",
  },
]

export function EvaluationBoundaryNote({
  slot,
  defaultOpen = false,
}: {
  slot?: EvaluationBoundarySlot
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="rounded-[4px] border border-[var(--line)] bg-[#f8fbef]">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-2 px-4 py-2.5 text-left"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="flex items-center gap-2 text-xs font-semibold text-[#3d5106]">
          <Info size={16} weight="fill" />
          调用 A / 调用 B / 维度：各自填什么、边界在哪
        </span>
        <CaretDown
          size={14}
          className={`shrink-0 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <div className="space-y-2 border-t border-[var(--line)] px-4 py-3">
          {ROWS.map((row) => {
            const active = slot === row.slot
            return (
              <div
                key={row.slot}
                className={`rounded-[4px] px-3 py-2 text-xs leading-5 ${
                  active
                    ? "border border-[var(--line-strong)] bg-white"
                    : "bg-white/60"
                }`}
              >
                <p className="font-semibold">
                  {row.title}
                  {active && (
                    <span className="ml-2 rounded-[3px] bg-[#3d5106] px-1.5 py-0.5 text-[0.6rem] font-semibold text-white">
                      当前配置位置
                    </span>
                  )}
                </p>
                <p className="mt-1 text-[var(--muted)]">{row.body}</p>
              </div>
            )
          })}
          <p className="px-1 pt-1 text-[0.68rem] leading-4 text-[var(--muted)]">
            提示：红线判定与媒介降权是确定性规则，必须由维度层执行，不能塞进调用 B 的提示词靠模型自觉，否则不可复现。
          </p>
        </div>
      )}
    </div>
  )
}
