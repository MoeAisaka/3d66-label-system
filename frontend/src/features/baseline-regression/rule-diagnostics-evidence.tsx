import type { BaselineRuleDiagnostics } from "@/lib/types"
import { SecondaryDrawer } from "@/components/workspace-page"
import type { ReactNode } from "react"

export function RuleDiagnosticsDrawer({
  open,
  onOpenChange,
  children,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  children: ReactNode
}) {
  return (
    <SecondaryDrawer
      open={open}
      onOpenChange={onOpenChange}
      title="扣分规则命中诊断"
      description="查看本轮回归中每条规则实际命中多少次；规则不命中时，调整扣分值与红线阈值不会改变结果。"
    >
      {children}
    </SecondaryDrawer>
  )
}

function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="border border-[var(--line)] px-3 py-2">
      <div className="text-xs text-[var(--muted)]">{label}</div>
      <div className="font-data mt-1 text-lg font-semibold">{value}</div>
      {hint ? <div className="mt-1 text-xs text-[var(--muted)]">{hint}</div> : null}
    </div>
  )
}

export function RuleDiagnosticsEvidence({
  data,
  loading,
  error,
}: {
  data?: BaselineRuleDiagnostics
  loading: boolean
  error: Error | null
}) {
  if (loading) {
    return <div className="h-52 animate-pulse bg-[#f7f9ef]" />
  }
  if (error) {
    return (
      <div className="border border-[#d7a09d] bg-[#fff5f4] px-4 py-4 text-sm text-[#8d2924]">
        规则命中诊断加载失败：{error.message}
      </div>
    )
  }
  if (!data) {
    return <div className="px-4 py-6 text-sm text-[var(--muted)]">暂无诊断数据。</div>
  }

  const hitRules = data.rules.filter((rule) => rule.hit_count > 0)
  const deadRules = data.rules.filter((rule) => rule.hit_count === 0)
  const noHitShare = data.scored_item_count
    ? Math.round((data.items_without_rule_hits / data.scored_item_count) * 100)
    : 0

  return (
    <div className="space-y-4" data-testid="baseline-rule-diagnostics">
      {/* 规则层几乎不参与打分时，先把结论说清楚：继续调阈值不会有效果。 */}
      {noHitShare >= 50 ? (
        <div
          className="border border-[#d8b070] bg-[#fdf7ea] px-4 py-3 text-sm text-[#7a5312]"
          data-testid="baseline-rule-layer-warning"
        >
          <div className="font-semibold">
            {data.rule_layer_inert
              ? "规则层完全未参与打分"
              : `${noHitShare}% 的样本没有命中任何规则`}
          </div>
          <div className="mt-1">
            这些样本的分数就是调用 B 给出的原始分，未经规则层增减
            {data.unpenalised_level
              ? `，落在 ${data.unpenalised_level} 档`
              : ""}
            。此时提高扣分值或下调红线阈值都不会改变判级，应先检查调用 B 是否报出了缺陷。
          </div>
        </div>
      ) : null}

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <Stat label="声明规则数" value={data.declared_rule_count} />
        <Stat
          label="从未命中"
          value={data.never_hit_rule_count}
          hint={data.declared_rule_count ? `占 ${Math.round((data.never_hit_rule_count / data.declared_rule_count) * 100)}%` : undefined}
        />
        <Stat
          label="零命中样本"
          value={`${data.items_without_rule_hits}/${data.scored_item_count}`}
          hint={`${noHitShare}%`}
        />
        <Stat label="触发分数上限" value={data.items_with_score_caps} hint="红线等策略生效次数" />
      </div>

      {Object.keys(data.redline_policy).length ? (
        <div className="border border-[var(--line)] px-4 py-3 text-sm">
          <div className="text-xs text-[var(--muted)]">红线策略</div>
          <div className="font-data mt-1 break-all">
            {Object.entries(data.redline_policy)
              .map(([key, value]) => `${key}=${String(value)}`)
              .join("  ")}
            {`  规则数=${data.redline_rule_count}`}
          </div>
          {data.redline_rule_count > 0 && data.items_with_score_caps === 0 ? (
            <div className="mt-1 text-xs text-[#7a5312]">
              红线已配置但本轮一次未触发。
            </div>
          ) : null}
        </div>
      ) : null}

      {data.undeclared_hits.length ? (
        <div className="border border-[#d7a09d] bg-[#fff5f4] px-4 py-3 text-sm text-[#8d2924]">
          <div className="font-semibold">模型报出了合同未声明的规则</div>
          <div className="font-data mt-1 break-all">
            {data.undeclared_hits
              .map((hit) => `${hit.dimension_key}/${hit.rule_id} × ${hit.hit_count}`)
              .join("，")}
          </div>
          <div className="mt-1 text-xs">这些命中不会参与扣分，通常说明提示词与合同的规则 id 不一致。</div>
        </div>
      ) : null}

      <section className="border border-[var(--line)]">
        <header className="grid grid-cols-[minmax(180px,1fr)_minmax(120px,180px)_80px_70px] gap-3 border-b border-[var(--line)] bg-[#f7f9ef] px-4 py-2 text-xs text-[var(--muted)]">
          <span>规则</span>
          <span>维度</span>
          <span>分值</span>
          <span>命中</span>
        </header>
        {data.rules.length === 0 ? (
          <div className="px-4 py-4 text-sm text-[var(--muted)]">该冻结合同未声明任何扣分或加分规则。</div>
        ) : (
          [...hitRules, ...deadRules].map((rule) => (
            <div
              key={`${rule.dimension_key}:${rule.rule_id}:${rule.kind}`}
              className="grid grid-cols-[minmax(180px,1fr)_minmax(120px,180px)_80px_70px] gap-3 border-b border-[var(--line)] px-4 py-2 text-sm last:border-0"
              data-testid={`baseline-rule-coverage-${rule.dimension_key}-${rule.rule_id}`}
            >
              <span className="font-data break-all">
                {rule.rule_id}
                <span className="ml-2 text-xs text-[var(--muted)]">
                  {rule.kind === "bonus" ? "加分" : "扣分"}
                </span>
                {rule.description ? (
                  <span className="mt-0.5 block text-xs text-[var(--muted)]">{rule.description}</span>
                ) : null}
              </span>
              <span className="break-all text-xs text-[var(--muted)]">
                {rule.dimension_label || rule.dimension_key}
              </span>
              <span className="font-data">{rule.points ?? "—"}</span>
              <span className={rule.hit_count ? "font-data font-semibold" : "font-data text-[#8d2924]"}>
                {rule.hit_count}
              </span>
            </div>
          ))
        )}
      </section>

      {Object.keys(data.score_buckets).length ? (
        <div className="border border-[var(--line)] px-4 py-3 text-sm">
          <div className="text-xs text-[var(--muted)]">分数分布</div>
          <div className="font-data mt-1 break-all">
            {Object.entries(data.score_buckets)
              .map(([bucket, count]) => `${bucket}: ${count}`)
              .join("　")}
          </div>
        </div>
      ) : null}
    </div>
  )
}
