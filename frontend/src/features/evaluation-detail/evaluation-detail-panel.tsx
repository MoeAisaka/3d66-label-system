import { CaretDown, Info, PencilSimple, WarningCircle } from "@phosphor-icons/react"
import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  buildEvaluationDetailSections,
  type DetailGroup,
  type DetailRow,
  type DetailSection,
} from "./detail-model"
import {
  RowCorrectionDialog,
  type RowCorrectionSubmit,
} from "./row-correction-dialog"

/**
 * 人工纠偏三段式评测细节面板。
 *
 * 固定按调用A → 调用B → 等级撮合器排列，让运营先看懂模型判了什么、凭什么判，
 * 再就地纠偏。每条可纠偏的信息都能点开弹窗填写正确结果与理由，可反复修改。
 * 由撮合器算出的行不给按钮，改为写明该去纠偏哪个上游判断。
 */
export function EvaluationDetailPanel({
  precheck,
  aesthetic,
  scoring,
  dimensionLabels,
  correctedFieldKeys,
  fieldSpecs,
  contractNodes,
  correctionHistory,
  onCorrect,
  correctionPending,
  correctionError,
  defaultOpenSection = "V3",
}: {
  precheck?: Record<string, unknown> | null
  aesthetic?: Record<string, unknown> | null
  scoring?: Record<string, unknown> | null
  dimensionLabels?: Record<string, string>
  correctedFieldKeys?: readonly string[]
  /** 机制下发的调用A字段规格，用于给新字段配中文名 */
  fieldSpecs?: Record<string, unknown> | null
  /** 冻结合同节点；决定哪一行可点击纠偏，不传则整个面板只读 */
  contractNodes?: readonly unknown[] | null
  /** 纠偏历史，用于在信息列表里并列展示人工结论 */
  correctionHistory?: readonly unknown[] | null
  /** 提交一条纠偏；不传则不显示纠偏按钮 */
  onCorrect?: (payload: RowCorrectionSubmit) => Promise<void> | void
  correctionPending?: boolean
  correctionError?: string | null
  /** 默认展开哪一段；撮合器是最常被质疑的一段，所以默认展开它 */
  defaultOpenSection?: DetailSection["key"] | "none"
}) {
  const sections = buildEvaluationDetailSections({
    precheck,
    aesthetic,
    scoring,
    dimensionLabels,
    correctedFieldKeys,
    fieldSpecs,
    contractNodes,
    correctionHistory,
  })
  const [editing, setEditing] = useState<DetailRow | null>(null)

  const correctable = Boolean(onCorrect)
  const totalCorrectable = correctable
    ? sections.reduce(
        (total, section) =>
          total
          + section.groups.reduce(
            (count, group) => count + group.rows.filter((row) => row.correction).length,
            0,
          ),
        0,
      )
    : 0

  return (
    <section
      className="border-y border-[var(--line-strong)] bg-white"
      aria-label="评测细节：调用A、调用B与等级撮合器"
      data-testid="evaluation-detail-panel"
    >
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-[var(--line-strong)] bg-[#fafbf8] px-5 py-4">
        <div>
          <p className="text-sm font-bold">评测细节</p>
          <p className="mt-1 text-[0.68rem] leading-5 text-[var(--muted)]">
            按实际执行顺序分为三段：调用A读图产字段、调用B判美感、等级撮合器出最终等级。
            {correctable && "点任意一条的「纠偏」即可修正，可反复修改。"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {correctable && <Badge tone="active">{totalCorrectable} 项可纠偏</Badge>}
          <Badge>{sections.length} 段</Badge>
        </div>
      </header>

      <div className="divide-y divide-[var(--line-strong)]">
        {sections.map((section, index) => (
          <DetailSectionBlock
            key={section.key}
            section={section}
            index={index + 1}
            defaultOpen={defaultOpenSection === section.key}
            onEdit={correctable ? setEditing : undefined}
          />
        ))}
      </div>

      {correctable && (
        <RowCorrectionDialog
          open={Boolean(editing)}
          onOpenChange={(next) => {
            if (!next) setEditing(null)
          }}
          rowLabel={editing?.label ?? ""}
          modelValue={editing?.value ?? "—"}
          target={editing?.correction ?? null}
          submitting={correctionPending}
          errorMessage={correctionError}
          onSubmit={async (payload) => {
            await onCorrect?.(payload)
            setEditing(null)
          }}
        />
      )}
    </section>
  )
}

function DetailSectionBlock({
  section,
  index,
  defaultOpen,
  onEdit,
}: {
  section: DetailSection
  index: number
  defaultOpen: boolean
  onEdit?: (row: DetailRow) => void
}) {
  const rowCount = section.groups.reduce((total, group) => total + group.rows.length, 0)
  return (
    <details
      open={defaultOpen}
      className="group bg-white"
      data-testid={`evaluation-detail-section-${section.key}`}
    >
      <summary className="grid cursor-pointer list-none grid-cols-[28px_minmax(0,1fr)_auto] items-center gap-3 px-5 py-4 hover:bg-[#fafbf8]">
        <span className="font-data text-xs text-[var(--muted)]">
          {String(index).padStart(2, "0")}
        </span>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-sm font-bold">{section.title}</p>
            <span className="font-data text-[0.68rem] text-[var(--muted)]">
              {rowCount} 项
            </span>
          </div>
          <p className="mt-1 text-[0.68rem] leading-5 text-[var(--muted)]">
            {section.description}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {section.headline && (
            <div className="text-right">
              <p className="font-data text-xl font-semibold leading-none">
                {section.headline}
              </p>
              {section.headlineHint && (
                <p className="mt-1 text-[0.68rem] text-[var(--muted)]">
                  {section.headlineHint}
                </p>
              )}
            </div>
          )}
          <CaretDown
            className="shrink-0 text-[var(--muted)] transition-transform group-open:rotate-180"
            size={16}
          />
        </div>
      </summary>

      <div className="border-t border-[var(--line)] bg-[#fbfcfa] px-5 py-4">
        {section.unavailableReason ? (
          <p className="flex items-start gap-2 border border-[#ead7a5] bg-[#fff9ea] px-3 py-3 text-xs leading-5 text-[#6b4b0b]">
            <WarningCircle className="mt-0.5 shrink-0" size={16} />
            {section.unavailableReason}
          </p>
        ) : (
          <div className="space-y-4">
            {section.groups.map((group) => (
              <DetailGroupBlock key={group.title} group={group} onEdit={onEdit} />
            ))}
          </div>
        )}
      </div>
    </details>
  )
}

function DetailGroupBlock({
  group,
  onEdit,
}: {
  group: DetailGroup
  onEdit?: (row: DetailRow) => void
}) {
  return (
    <div className="border border-[var(--line)] bg-white">
      <div className="border-b border-[var(--line)] bg-[#fafbf8] px-3 py-2.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs font-bold">{group.title}</p>
          <span className="font-data text-[0.68rem] text-[var(--muted)]">
            {group.rows.length} 项
          </span>
        </div>
        {group.description && (
          <p className="mt-1 text-[0.68rem] leading-5 text-[var(--muted)]">
            {group.description}
          </p>
        )}
      </div>

      {group.rows.length ? (
        <div className="divide-y divide-[var(--line)]">
          {group.rows.map((row, index) => (
            <DetailRowBlock key={`${row.label}-${index}`} row={row} onEdit={onEdit} />
          ))}
        </div>
      ) : (
        <p className="px-3 py-3 text-xs leading-5 text-[var(--muted)]">
          {group.emptyText || "本段没有可展示的内容。"}
        </p>
      )}

      {group.note && (
        <p className="flex items-start gap-2 border-t border-[var(--line)] bg-[#fafbf8] px-3 py-2.5 text-[0.68rem] leading-5 text-[var(--muted)]">
          <Info className="mt-0.5 shrink-0" size={13} />
          {group.note}
        </p>
      )}
    </div>
  )
}

function DetailRowBlock({
  row,
  onEdit,
}: {
  row: DetailRow
  onEdit?: (row: DetailRow) => void
}) {
  const evidence = row.evidence?.filter(Boolean) ?? []
  const canCorrect = Boolean(onEdit && row.correction)
  // 人工值与模型值相同就不必并列，避免制造「改了但看不出差别」的噪音
  const showHumanValue = row.humanValue !== undefined && row.humanValue !== row.value

  return (
    <div
      className={`grid gap-2 px-3 py-3 md:grid-cols-[minmax(0,180px)_minmax(0,1fr)_auto] ${
        showHumanValue ? "bg-[#fbfdeb]" : ""
      }`}
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <p className={`text-xs font-semibold ${row.isNew ? "font-data" : ""}`}>{row.label}</p>
          {row.isNew && <Badge tone="warning">新增</Badge>}
          {(row.corrected || showHumanValue) && <Badge tone="active">已人工纠偏</Badge>}
        </div>
        {row.hint && (
          <p className="mt-1 text-[0.68rem] leading-4 text-[var(--muted)]">{row.hint}</p>
        )}
      </div>

      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          {showHumanValue && (
            <span className="font-data text-[0.68rem] text-[var(--muted)]">模型</span>
          )}
          {row.tone && row.tone !== "neutral" ? (
            <Badge tone={row.tone}>{row.value}</Badge>
          ) : (
            <p
              className={`whitespace-pre-wrap break-words text-sm leading-6 ${
                showHumanValue ? "text-[var(--muted)] line-through" : ""
              }`}
            >
              {row.value}
            </p>
          )}
        </div>

        {showHumanValue && (
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <span className="font-data text-[0.68rem] text-[#4b6a10]">人工</span>
            <p className="whitespace-pre-wrap break-words text-sm font-semibold leading-6">
              {row.humanValue}
            </p>
          </div>
        )}

        {evidence.length > 0 && (
          <ul className="mt-2 space-y-1.5">
            {evidence.map((text, index) => (
              <li
                key={`${text}-${index}`}
                className="grid grid-cols-[10px_minmax(0,1fr)] gap-2 text-xs leading-5 text-[var(--muted)]"
              >
                <span className="mt-[0.5rem] size-1.5 bg-[var(--line-strong)]" />
                <span className="break-words">{text}</span>
              </li>
            ))}
          </ul>
        )}

        {!canCorrect && row.derivedNote && (
          <p className="mt-1.5 text-[0.68rem] leading-4 text-[var(--muted)]">
            {row.derivedNote}
          </p>
        )}
      </div>

      <div className="md:pl-2">
        {canCorrect && (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => onEdit?.(row)}
            aria-label={`纠偏${row.label}`}
          >
            <PencilSimple />纠偏
          </Button>
        )}
      </div>
    </div>
  )
}
