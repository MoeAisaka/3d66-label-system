import { useRef, useState } from "react"
import { ArrowLeft, FileXls, ShieldCheck, UploadSimple, WarningCircle } from "@phosphor-icons/react"
import { useMutation } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { toast } from "sonner"

import { PageHeader } from "@/components/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import type { HistoricalCorrectionPreview } from "@/lib/types"

const roleNames = {
  target_error: "目标错例",
  stable_control: "稳定对照",
  blind_holdout: "锁定盲测集",
  reason_only: "原因专项集",
} as const

export function HistoricalCorrectionsPage() {
  const inputRef = useRef<HTMLInputElement>(null)
  const [files, setFiles] = useState<File[]>([])
  const [preview, setPreview] = useState<HistoricalCorrectionPreview | null>(null)
  const upload = useMutation({
    mutationFn: async () => {
      const body = new FormData()
      files.forEach((file) => body.append("files", file))
      return api<HistoricalCorrectionPreview>("/api/historical-corrections/preview", { method: "POST", body })
    },
    onSuccess: (data) => {
      setPreview(data)
      toast.success(`已只读解析 ${data.summary.unique_item_count} 条高置信人工纠偏记录`)
    },
    onError: (error) => toast.error(error.message),
  })

  return (
    <>
      <PageHeader
        index="07"
        title="历史人工纠偏预览"
        description="只读解析“已处理样本3d&SU”表格，完成字段映射、去重和确定性分层；不会写入素材、Gold或触发模型。"
        actions={<Button asChild variant="secondary"><Link to="/sample-sets"><ArrowLeft />返回样本与回归</Link></Button>}
      />
      <div className="mx-auto shell-content px-5 py-7 md:px-8 lg:px-10 lg:py-9">
        <section className="grid gap-5 border-y border-[var(--line-strong)] bg-white p-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
          <div>
            <div className="flex items-center gap-2"><FileXls size={22} /><h2 className="text-lg font-bold">选择历史纠偏表格</h2></div>
            <p className="mt-2 max-w-[80ch] text-sm leading-6 text-[var(--muted)]">支持同时选择多份 XLSX。预览保留来源文件与行号；缺少当前维度评分或规则版本的记录不会被伪装成可比较 Gold。</p>
            <input ref={inputRef} type="file" accept=".xlsx" multiple className="sr-only" onChange={(event) => { setFiles(Array.from(event.target.files ?? [])); setPreview(null) }} />
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <Button variant="secondary" onClick={() => inputRef.current?.click()}><UploadSimple />选择 XLSX</Button>
              <span className="text-xs text-[var(--muted)]">{files.length ? `已选择 ${files.length} 份：${files.map((file) => file.name).join("、")}` : "尚未选择文件"}</span>
            </div>
          </div>
          <Button onClick={() => upload.mutate()} disabled={!files.length || upload.isPending}>{upload.isPending ? "正在安全解析" : "生成只读预览"}<ShieldCheck /></Button>
        </section>

        <div className="mt-4 flex items-start gap-2 border border-[#e5d5b8] bg-[#fff9ef] p-4 text-xs leading-5 text-[#7d4308]">
          <WarningCircle className="mt-0.5 shrink-0" />
          此入口当前只负责预览与分层，不会自动导入。正式入库前仍需人工抽检映射、确认资产键并批准Gold候选。
        </div>

        {preview && <>
          <section className="mt-7 grid grid-cols-2 gap-px border-y border-[var(--line-strong)] bg-[var(--line)] md:grid-cols-3 xl:grid-cols-6">
            <Metric value={preview.summary.unique_item_count + preview.summary.duplicate_count} label="读取记录" />
            <Metric value={preview.summary.unique_item_count} label="去重后记录" />
            <Metric value={preview.summary.role_counts.target_error ?? 0} label="目标错例" />
            <Metric value={preview.summary.role_counts.stable_control ?? 0} label="稳定对照" />
            <Metric value={preview.summary.role_counts.blind_holdout ?? 0} label="锁定盲测" />
            <Metric value={preview.summary.role_counts.reason_only ?? 0} label="原因专项" />
          </section>

          <section className="mt-7 border-y border-[var(--line-strong)] bg-white">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--line)] px-4 py-4">
              <div><h2 className="text-lg font-bold">分层预览</h2><p className="mt-1 text-xs text-[var(--muted)]">当前展示前 {Math.min(200, preview.items.length)} 条，分层由稳定资产键决定，避免重复上传改变盲测集合。</p></div>
              <Badge>{preview.files.length} 份来源文件</Badge>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1120px] border-collapse text-left text-sm">
                <thead><tr className="border-b border-[var(--line)] bg-[#fafbf8] text-xs text-[var(--muted)]"><th className="px-4 py-3">来源</th><th className="px-3 py-3">去重键</th><th className="px-3 py-3">模型等级</th><th className="px-3 py-3">人工等级</th><th className="px-3 py-3">人工原因</th><th className="px-4 py-3">样本角色</th></tr></thead>
                <tbody>{preview.items.slice(0, 200).map((item) => <tr key={item.provenance.source_row_sha256} className="border-b border-[var(--line)] last:border-0"><td className="px-4 py-3"><p className="max-w-52 truncate font-semibold" title={item.provenance.source_file}>{item.provenance.source_file}</p><p className="font-data mt-1 text-[0.68rem] text-[var(--muted)]">{item.provenance.sheet} · 第 {item.provenance.source_row} 行</p></td><td className="font-data max-w-56 truncate px-3 py-3 text-xs" title={item.dedupe_key}>{item.dedupe_key}</td><td className="font-data px-3 py-3 text-lg">{String(item.correction_candidate.model_level || "—")}</td><td className="font-data px-3 py-3 text-lg font-bold">{String(item.correction_candidate.human_level || "—")}</td><td className="max-w-96 px-3 py-3 text-xs leading-5 text-[var(--muted)]">{item.correction_candidate.reason || "—"}</td><td className="px-4 py-3"><Badge tone={item.sample_role === "blind_holdout" ? "warning" : item.sample_role === "stable_control" ? "success" : "active"}>{roleNames[item.sample_role]}</Badge></td></tr>)}</tbody>
              </table>
            </div>
          </section>
        </>}
      </div>
    </>
  )
}

function Metric({ value, label }: { value: number; label: string }) {
  return <div className="bg-white p-5"><p className="font-data text-2xl font-semibold">{value}</p><p className="mt-1 text-xs text-[var(--muted)]">{label}</p></div>
}
