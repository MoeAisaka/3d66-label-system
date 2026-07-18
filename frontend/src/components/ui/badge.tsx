import type { HTMLAttributes } from "react"

import { cn } from "@/lib/utils"

const tones = {
  neutral: "border-[var(--line-strong)] bg-white text-[#4f554d]",
  active: "border-[#a5c522] bg-[#f0f8c8] text-[#263000]",
  warning: "border-[#e5c9a7] bg-[#fff6e9] text-[#7d4308]",
  danger: "border-[#e8c1bd] bg-[#fff0ee] text-[#8d2924]",
  success: "border-[#bdd8c7] bg-[#edf7f0] text-[#245b3b]",
}

export function Badge({
  className,
  tone = "neutral",
  ...props
}: HTMLAttributes<HTMLSpanElement> & { tone?: keyof typeof tones }) {
  return (
    <span
      className={cn(
        "inline-flex min-h-6 items-center rounded-[4px] border px-2 py-0.5 text-xs font-semibold leading-none",
        tones[tone],
        className,
      )}
      {...props}
    />
  )
}
