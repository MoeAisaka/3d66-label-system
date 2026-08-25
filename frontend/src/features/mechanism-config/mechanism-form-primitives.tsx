/** 表单原语 —— 从 image-rule-editor.tsx 抽出,无业务语义,仅样式与容器。 */

import { Plus, Trash } from "@phosphor-icons/react"
import type { ReactNode } from "react"

export const inputClass = "h-9 w-full rounded-[4px] border border-[var(--line-strong)] px-2 text-xs"
export const numberClass = "h-9 w-24 rounded-[4px] border border-[var(--line-strong)] px-2 text-xs font-data"

export function FieldCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border border-[var(--line)] bg-white">
      <div className="border-b border-[var(--line)] px-4 py-3">
        <h3 className="text-sm font-bold">{title}</h3>
      </div>
      <div className="px-4 py-4">{children}</div>
    </section>
  )
}

export function IconButton({ onClick, title, danger }: { onClick: () => void; title: string; danger?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={`inline-flex h-8 w-8 items-center justify-center rounded-[4px] border border-[var(--line-strong)] bg-white [&_svg]:size-4 ${
        danger ? "text-[#8d2924] hover:bg-[#fdf3f2]" : "hover:bg-[#f8f9f6]"
      }`}
    >
      {danger ? <Trash /> : <Plus />}
    </button>
  )
}
