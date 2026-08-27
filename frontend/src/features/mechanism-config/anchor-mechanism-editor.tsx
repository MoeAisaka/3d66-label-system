/** 锚点图机制编辑器 —— 只管「哪些图片代表哪个等级」。
 *
 * 架构约束（2026-08-24 裁决）：本面板只写 anchor_mechanism 块里的锚点图片、
 * 等级标注、送图上限与开关。分数阈值归合同顶层 level_scale、维度与权重归
 * Call B 的 dimensions、红线与硬伤封顶归 Call A 与 redline_policy —— 这类旋钮
 * 一个都不准出现在本面板，否则「换一张锚图看分数怎么变」就失去可归因性。
 *
 * 锚点图必须是素材库里已有的素材：合同只存 asset_id + sha256，后端
 * resolve_frozen_anchor_assets 只能从 Asset 表解析。要用新图先走素材库上传。
 */

import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Trash, Warning } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import { baselineRegressionApi } from "@/lib/api"
import type { Asset } from "@/lib/types"
import type { Editable } from "./types"
import { FieldCard, inputClass, numberClass } from "./mechanism-form-primitives"
import {
  ANCHOR_LEVELS,
  ANCHOR_MECHANISM_KEY,
  ANCHOR_MIME_TYPES,
  MAX_ANCHOR_IMAGES_CEILING,
  anchorMechanismIntruders,
  readAnchorMechanism,
  removeAnchorMechanismAnchor,
  setAnchorMechanismEnabled,
  setAnchorMechanismMaxImages,
  upsertAnchorMechanismAnchor,
  type AnchorLevel,
} from "./image-rule-contract"

/** 素材能否作为锚点图；不能时给出人能看懂的原因，而不是静默过滤掉。 */
function ineligibleReason(asset: Asset): string | null {
  if (!(ANCHOR_MIME_TYPES as readonly string[]).includes(asset.mime_type)) {
    return `格式 ${asset.mime_type} 不支持，仅限 JPEG / PNG`
  }
  if (!asset.sha256) return "缺少内容哈希，无法冻结图片身份"
  return null
}

export function AnchorMechanismEditor({
  draft,
  onPatch,
}: {
  draft: Editable
  onPatch: (mutator: (next: Editable) => void) => void
}) {
  const [notice, setNotice] = useState<string | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [keyword, setKeyword] = useState("")
  const [onlyThisCategory, setOnlyThisCategory] = useState(true)
  const [pendingAsset, setPendingAsset] = useState<Asset | null>(null)

  const view = readAnchorMechanism(draft.contract)
  const intruders = anchorMechanismIntruders(draft.contract[ANCHOR_MECHANISM_KEY])

  // 关键词走服务端检索：素材库数千条，只拉一页再本地过滤会让页外素材永远搜不到
  // （运营反馈「素材库里明明有、就是搜不出来」的根因）。
  const trimmedKeyword = keyword.trim()
  const assets = useQuery({
    queryKey: [
      "anchor-mechanism-assets",
      onlyThisCategory ? draft.category_key : "__all__",
      trimmedKeyword,
    ],
    queryFn: () =>
      baselineRegressionApi.listAssets(
        undefined,
        onlyThisCategory ? draft.category_key : undefined,
        0,
        200,
        trimmedKeyword || undefined,
      ),
    enabled: pickerOpen,
  })

  const alreadyUsed = useMemo(
    () => new Set(view.anchors.map((a) => a.assetId)),
    [view.anchors],
  )

  const candidates = useMemo(() => {
    const items = assets.data?.items ?? []
    // 关键词已由服务端匹配，这里只排除已配为锚点的素材。
    return items.filter((asset) => !alreadyUsed.has(asset.id))
  }, [assets.data?.items, alreadyUsed])

  function addAnchor(asset: Asset, level: AnchorLevel) {
    const reason = ineligibleReason(asset)
    if (reason) {
      setNotice(`素材 ${asset.id} 不可用作锚点图：${reason}`)
      return
    }
    let failure: string | null = null
    onPatch((next) => {
      const result = upsertAnchorMechanismAnchor(next.contract, {
        level,
        assetId: asset.id,
        mimeType: asset.mime_type,
        sha256: asset.sha256!,
      })
      if (!result.ok) failure = result.reason
    })
    setNotice(failure)
    if (!failure) {
      setPendingAsset(null)
      setPickerOpen(false)
    }
  }

  return (
    <FieldCard title="锚点图机制">
      <p className="text-[0.68rem] leading-relaxed text-[var(--muted)]">
        只配「哪些图片代表哪个等级」。分数阈值在<b>等级刻度</b>、维度与权重在
        <b>调用 B 维度</b>、红线与硬伤封顶在<b>红线与人工复核</b>—— 这里不重复设置，
        这样换一张锚图后分数怎么变才归因得清。锚点图取自素材库，需要新图请先上传素材。
      </p>

      {intruders.length > 0 && (
        <div className="flex items-start gap-2 border border-[#e0b4b0] bg-[#fdf3f2] px-3 py-2 text-[0.72rem] text-[#8d2924]">
          <Warning className="mt-[2px] size-4 shrink-0" />
          <span>
            合同里的锚点机制块混入了不属于它的机制：{intruders.join("、")}。
            保存会被后端隔离守卫拒绝，请把这些设置移回各自的面板。
          </span>
        </div>
      )}

      <label className="flex items-center gap-2 text-[0.78rem]">
        <input
          type="checkbox"
          checked={view.present && view.enabled}
          onChange={(e) =>
            onPatch((next) => {
              setAnchorMechanismEnabled(next.contract, e.target.checked)
            })
          }
        />
        <span className="font-semibold">启用锚点图机制</span>
        <span className="text-[0.68rem] text-[var(--muted)]">
          关闭即从合同移除整块；重新开启会新建空块，不依赖历史修订
        </span>
      </label>

      {view.present && view.enabled && (
        <>
          <div className="flex flex-wrap items-center gap-4 text-[0.78rem]">
            <label className="flex items-center gap-2">
              <span className="font-semibold">送图上限</span>
              <input
                type="number"
                min={1}
                max={MAX_ANCHOR_IMAGES_CEILING}
                className={numberClass}
                value={view.maxAnchorImages}
                onChange={(e) => {
                  const parsed = Number(e.target.value)
                  let failure: string | null = null
                  onPatch((next) => {
                    const result = setAnchorMechanismMaxImages(next.contract, parsed)
                    if (!result.ok) failure = result.reason
                  })
                  setNotice(failure)
                }}
              />
              <span className="text-[0.68rem] text-[var(--muted)]">
                上限 {MAX_ANCHOR_IMAGES_CEILING}
              </span>
            </label>
            <div className="flex items-center gap-2">
              <span className="font-semibold">等级覆盖</span>
              {ANCHOR_LEVELS.map((level) => {
                const covered = view.levelsCovered.includes(level)
                return (
                  <span
                    key={level}
                    className={`inline-flex h-6 min-w-8 items-center justify-center rounded-[4px] border px-1 text-[0.68rem] ${
                      covered
                        ? "border-[var(--line-strong)] bg-[#f0f4ec] font-semibold"
                        : "border-dashed border-[var(--line)] text-[var(--muted)]"
                    }`}
                    title={covered ? `${level} 已配锚点图` : `${level} 未配锚点图`}
                  >
                    {level}
                  </span>
                )
              })}
              <span className="text-[0.68rem] text-[var(--muted)]">
                不要求五档齐全，可以先配两档开始调
              </span>
            </div>
          </div>

          {view.enabled && view.anchors.length > 0 && (
            // 锚图是随请求一起发给模型的额外图片，调用B正文若不声明这件事，
            // 模型会把锚点当成待评图的一部分来评价——运营写正文时必踩的坑。
            <div className="border border-[#cfd9c4] bg-[#f4f8ef] px-3 py-2 text-[0.72rem] leading-5">
              <span className="font-semibold">调用B正文需配套说明</span>
              ：启用后每次请求会发出{" "}
              <span className="font-data">{view.anchors.length + 1}</span> 张图——
              前 <span className="font-data">{view.anchors.length}</span> 张是锚点（按
              {" "}{view.anchors.map((a) => a.level).join("、")} 顺序），
              <span className="font-semibold">最后一张才是待评图</span>。
              调用B正文必须写明这一点并要求与锚点做相对比较，否则模型会把锚点当作待评图内容来评价；
              还应禁止把锚点图的内容写进待评图的证据里。锚点只改变参照系，不改变输出结构。
            </div>
          )}

          {notice && (
            <div className="border border-[#e0b4b0] bg-[#fdf3f2] px-3 py-2 text-[0.72rem] text-[#8d2924]">
              {notice}
            </div>
          )}

          <div className="space-y-2">
            {view.anchors.length === 0 && (
              <p className="text-[0.72rem] text-[var(--muted)]">
                还没有锚点图。点下方「挑选锚点图」从素材库添加。
              </p>
            )}
            {view.anchors.map((anchor) => (
              <div
                key={anchor.assetId}
                className="grid gap-2 border border-[var(--line)] px-3 py-2 sm:grid-cols-[64px_90px_1fr_auto] sm:items-center"
              >
                <img
                  src={`/api/assets/${anchor.assetId}/file`}
                  alt={`锚点图 ${anchor.assetId}`}
                  className="h-12 w-16 border border-[var(--line)] object-cover"
                  loading="lazy"
                />
                <select
                  className={inputClass}
                  value={anchor.level}
                  onChange={(e) => {
                    const level = e.target.value as AnchorLevel
                    let failure: string | null = null
                    onPatch((next) => {
                      const result = upsertAnchorMechanismAnchor(next.contract, {
                        ...anchor,
                        level,
                      })
                      if (!result.ok) failure = result.reason
                    })
                    setNotice(failure)
                  }}
                >
                  {ANCHOR_LEVELS.map((level) => (
                    <option key={level} value={level}>
                      {level}
                    </option>
                  ))}
                </select>
                <div className="space-y-1">
                  <input
                    className={inputClass}
                    placeholder="备注：这张图为什么代表该等级（可留空）"
                    value={anchor.note ?? ""}
                    onChange={(e) =>
                      onPatch((next) => {
                        upsertAnchorMechanismAnchor(next.contract, {
                          ...anchor,
                          note: e.target.value,
                        })
                      })
                    }
                  />
                  <p className="text-[0.62rem] text-[var(--muted)]">
                    素材 #{anchor.assetId} · {anchor.mimeType} ·{" "}
                    {anchor.sha256.slice(0, 12)}…
                  </p>
                </div>
                <button
                  type="button"
                  title="移除该锚点图"
                  onClick={() => {
                    onPatch((next) => {
                      removeAnchorMechanismAnchor(next.contract, anchor.assetId)
                    })
                    setNotice(null)
                  }}
                  className="inline-flex h-8 w-8 items-center justify-center rounded-[4px] border border-[var(--line-strong)] bg-white text-[#8d2924] hover:bg-[#fdf3f2] [&_svg]:size-4"
                >
                  <Trash />
                </button>
              </div>
            ))}
          </div>

          {!pickerOpen ? (
            <Button
              type="button"
              variant="secondary"
              onClick={() => {
                setPickerOpen(true)
                setNotice(null)
              }}
            >
              挑选锚点图
            </Button>
          ) : (
            <div className="space-y-2 border border-[var(--line-strong)] bg-[#fafbf8] px-3 py-3">
              <div className="flex flex-wrap items-center gap-3">
                <input
                  className={inputClass}
                  placeholder="按素材名或 ID 搜索"
                  value={keyword}
                  onChange={(e) => setKeyword(e.target.value)}
                />
                <label className="flex items-center gap-2 text-[0.72rem]">
                  <input
                    type="checkbox"
                    checked={onlyThisCategory}
                    onChange={(e) => setOnlyThisCategory(e.target.checked)}
                  />
                  只看本类目素材
                </label>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => {
                    setPickerOpen(false)
                    setPendingAsset(null)
                  }}
                >
                  收起
                </Button>
              </div>

              {assets.isLoading && (
                <p className="text-[0.72rem] text-[var(--muted)]">正在读素材库…</p>
              )}
              {assets.isError && (
                <p className="text-[0.72rem] text-[#8d2924]">
                  素材库读取失败，请重试或检查权限。
                </p>
              )}
              {!assets.isLoading && !assets.isError && candidates.length === 0 && (
                <p className="text-[0.72rem] text-[var(--muted)]">
                  {trimmedKeyword
                    ? onlyThisCategory
                      ? `本类目内没有匹配「${trimmedKeyword}」的素材。该图若属于其它类目，请取消勾选「只看本类目素材」再搜。`
                      : `没有匹配「${trimmedKeyword}」的素材。可按素材名称或资产编号搜索。`
                    : "没有可选素材。已配为锚点图的素材不会重复出现。"}
                </p>
              )}

              <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-4">
                {candidates.map((asset) => {
                  const reason = ineligibleReason(asset)
                  const selected = pendingAsset?.id === asset.id
                  return (
                    <button
                      key={asset.id}
                      type="button"
                      disabled={Boolean(reason)}
                      title={reason ?? `选择素材 #${asset.id}`}
                      onClick={() => setPendingAsset(asset)}
                      className={`flex flex-col gap-1 border px-2 py-2 text-left text-[0.68rem] ${
                        reason
                          ? "cursor-not-allowed border-dashed border-[var(--line)] opacity-60"
                          : selected
                            ? "border-[var(--line-strong)] bg-[#f0f4ec]"
                            : "border-[var(--line)] bg-white hover:bg-[#f8f9f6]"
                      }`}
                    >
                      <img
                        src={asset.image_url ?? `/api/assets/${asset.id}/file`}
                        alt={asset.name}
                        className="h-20 w-full border border-[var(--line)] object-cover"
                        loading="lazy"
                      />
                      <span className="truncate font-semibold" title={asset.name}>
                        {asset.name}
                      </span>
                      <span className="text-[var(--muted)]">
                        #{asset.id}
                        {reason ? ` · ${reason}` : ""}
                      </span>
                    </button>
                  )
                })}
              </div>

              {pendingAsset && (
                <div className="flex flex-wrap items-center gap-2 border-t border-[var(--line)] pt-2 text-[0.72rem]">
                  <span className="font-semibold">
                    把「{pendingAsset.name}」设为哪个等级的锚点图：
                  </span>
                  {ANCHOR_LEVELS.map((level) => (
                    <Button
                      key={level}
                      type="button"
                      variant="secondary"
                      onClick={() => addAnchor(pendingAsset, level)}
                    >
                      {level}
                    </Button>
                  ))}
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => setPendingAsset(null)}
                  >
                    取消
                  </Button>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </FieldCard>
  )
}
