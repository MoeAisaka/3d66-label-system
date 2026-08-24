import type { BaselineRegressionItem } from "@/lib/types"
import { percent } from "@/features/baseline-regression/regression-page-shared"

export function LevelExplanation({ item }: { item: BaselineRegressionItem }) {
  const explanation = item.level_explanation
  const interpretation = item.interpretation
  const labels = Object.fromEntries(
    item.evaluation?.dimension_schema.definition?.dimensions.map((dimension) => [
      dimension.key,
      dimension.label,
    ]) ?? [],
  )
  if (explanation.status === "unavailable_historical") {
    return (
      <p className="border-t border-[var(--line)] px-4 py-4 text-xs text-[var(--muted)]">
        {explanation.message ?? "历史结果未冻结评测理由"}
      </p>
    )
  }
  const dimensionRows = (explanation.all_dimensions.length
    ? explanation.all_dimensions
    : [
        ...explanation.strong_dimensions,
        ...explanation.weak_dimensions,
      ].filter(
        (dimension, index, all) =>
          all.findIndex((candidate) => candidate.key === dimension.key) === index,
      )
  ).map((dimension) => ({
    ...dimension,
    kind: dimension.grade >= 4
      ? "主要优势"
      : dimension.grade <= 2
        ? "主要短板"
        : "中性维度",
  }))
  return (
    <div className="border-t border-[var(--line)] px-4 py-4">
      {interpretation?.status === "manual_required" ? (
        <div className="border-l-2 border-[#c98a1f] bg-[#fff9ea] px-3 py-2 text-xs leading-5 text-[#7d4308]">
          <strong>自由输出已正常完成，等待人工判断。</strong>
          {interpretation.message ? ` ${interpretation.message}` : " 本次未形成可安全比较的 L1–L5 等级，因此不进入自动准确率分母。"}
        </div>
      ) : (
        <p className="text-sm font-semibold">
          服务端结论：{explanation.predicted_level ?? "未形成等级"} ·
          {" "}{explanation.authoritative_score ?? "无有效分数"} 分
        </p>
      )}
      {(interpretation?.raw_text_a || interpretation?.raw_text_b) && (
        <div className="mt-4 grid gap-3 lg:grid-cols-2">
          {interpretation.raw_text_a && (
            <RawModelOutput label="调用 A 原始输出" value={interpretation.raw_text_a} />
          )}
          {interpretation.raw_text_b && (
            <RawModelOutput label="调用 B 原始输出" value={interpretation.raw_text_b} />
          )}
        </div>
      )}
      {explanation.status === "out_of_scope" && (
        <p className="mt-2 text-xs text-[#8d2924]">素材超出评测范围，未形成正式美感等级。</p>
      )}
      {dimensionRows.length > 0 && (
        <div className="mt-4 divide-y divide-[var(--line)] border-y border-[var(--line)]">
          {dimensionRows.map((dimension) => (
            <div
              key={dimension.key}
              className="grid gap-2 py-3 text-xs sm:grid-cols-[88px_120px_minmax(0,1fr)]"
            >
              <span className="font-semibold text-[var(--muted)]">{dimension.kind}</span>
              <span className="font-semibold">
                {labels[dimension.key] ?? dimension.key} · {dimension.grade} 级
              </span>
              <span className="leading-5 text-[var(--muted)]">
                {[...dimension.evidence, ...dimension.defects].join("；") || "未返回可展示证据"}
              </span>
            </div>
          ))}
        </div>
      )}
      <div className="mt-4 grid gap-3 border-y border-[var(--line)] py-3 text-xs sm:grid-cols-2">
        <p>
          <span className="font-semibold text-[var(--muted)]">模型置信度：</span>
          {item.confidence === null || item.confidence === undefined
            ? "未返回"
            : percent(item.confidence)}
        </p>
        <p>
          <span className="font-semibold text-[var(--muted)]">复核标记：</span>
          {item.needs_review === true
            ? "建议人工复核"
            : item.needs_review === false
              ? "未触发复核"
              : "未记录"}
        </p>
        <p>
          <span className="font-semibold text-[var(--muted)]">画质：</span>
          {explanation.image_quality.status === "available"
            ? `${explanation.image_quality.severity_label || explanation.image_quality.severity || "已返回"}${explanation.image_quality.confidence === null || explanation.image_quality.confidence === undefined ? "" : ` · ${percent(explanation.image_quality.confidence)} 置信度`}`
            : "未返回画质证据"}
        </p>
        <p>
          <span className="font-semibold text-[var(--muted)]">版本：</span>
          {[item.versions.model, item.versions.rubric, item.versions.engine]
            .filter(Boolean)
            .join(" · ") || "历史结果未记录"}
        </p>
      </div>
      {explanation.image_quality.evidence.length > 0 && (
        <p className="mt-2 text-xs leading-5 text-[var(--muted)]">
          画质证据：{explanation.image_quality.evidence.join("；")}
        </p>
      )}
      {explanation.caps.length > 0 && (
        <p className="mt-3 text-xs leading-5 text-[#7d4308]">
          等级限制：{explanation.caps.map((cap) => {
            const level = typeof cap.cap === "string" ? cap.cap : ""
            const reason = typeof cap.reason === "string" ? cap.reason : "触发等级限制"
            return [level, reason].filter(Boolean).join(" · ")
          }).join("；")}
        </p>
      )}
      {explanation.review_reasons.length > 0 && (
        <p className="mt-2 text-xs leading-5 text-[var(--muted)]">
          建议人工复核：{explanation.review_reasons.join("；")}
        </p>
      )}
    </div>
  )
}

export function levelExplanationSummary(item: BaselineRegressionItem) {
  if (item.interpretation?.status === "manual_required") {
    return "自由输出已完整保存；未强制转换为八维或等级，等待人工判断"
  }
  const explanation = item.level_explanation
  if (explanation.status === "unavailable_historical") {
    return explanation.message ?? "历史结果未冻结评测理由"
  }
  if (explanation.status === "out_of_scope") {
    return "超出评测范围，未形成正式等级"
  }
  const weakest = explanation.weak_dimensions[0]
  const weakEvidence = weakest
    ? [...weakest.defects, ...weakest.evidence][0]
    : null
  const cap = explanation.caps[0]
  const capReason = cap && typeof cap.reason === "string" ? cap.reason : null
  return capReason
    ? `等级受限：${capReason}`
    : weakEvidence
      ? `主要短板：${weakEvidence}`
      : `服务端按 ${explanation.authoritative_score ?? "—"} 分判定为 ${explanation.predicted_level ?? "未定级"}`
}

export function RawModelOutput({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 border-y border-[var(--line)] bg-white px-3 py-3">
      <p className="text-xs font-bold text-[var(--muted)]">{label}</p>
      <pre className="font-data mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words text-xs leading-5 text-foreground">{value}</pre>
    </div>
  )
}
