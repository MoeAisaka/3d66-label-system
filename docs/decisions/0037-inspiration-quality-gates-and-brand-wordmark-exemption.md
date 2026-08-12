# ADR-0037：灵感图锚点校准、随手拍质量闸门与品牌字样窄豁免

- 状态：Accepted
- 日期：2026-08-07

## 背景

冻结 10 张金丝雀 run 18 的工程链路已通过，但精确命中率仅 40%，L3 召回为 0%。
离线 trace 显示：调用 B v4 虽保留严格八维证据结构，却丢失了四张 Owner 锚图的
可见语义，清晰且无明显硬伤的普通图片被系统性抬高；asset 390 同时命中“是随手拍”
与 `blurry_grayish`，却因联合条件未包含该硬伤而未进入 L5；asset 747 的 TEKLA
品牌字样被调用 A 记为 `subject_obscuring_watermark`，与真实半透明版权水印共用 Tier A。

## 决策

1. 调用 A rev4 合同与提示词保持不变。新增不可变调用 B v5，恢复 2045/747/1263/601
   四锚的可见内容、相邻边界和 75/90 分边界说明；严格八维 evidence 合同不变。
2. 新 inspiration v3 revision 将 `blurry_grayish` 加入“是随手拍”红线的联合硬伤，
   命中即 L5 并跳过调用 B。
3. “是随手拍”但未命中联合硬伤时不判红线，仅确定性软封顶 59/L4，避免在 L4/L5
   间随模型波动。
4. `subject_obscuring_watermark` 只在以下条件全部满足时排除 Tier A：调用 A 决定性证据
   明示“品牌文字/品牌字样”；调用 B 的 `detail_completion` 与
   `presentation_integrity` 均至少 grade 4，且两个维度 shortcomings 均为空。
   任一证据缺失、形状漂移或条件不满足，均保留原 Tier A。
5. 这些规则只存在于新 inspiration v3 revision；proposal_text_pdf、其他类目、旧 prompt
   与旧 revision 不改写。基础美感分与八维证据继续只读冻结。

## 后果

- L3/L1 的模型校准由可审计锚点语义承担，不改等级阈值，也不把规则结果反写基础分。
- 随手拍 L5 需要确定性联合信号；未达红线的随手拍稳定落 L4。
- 品牌字样豁免是双来源、双维度的窄豁免；真实版权/半透明水印以及证据不完整样本仍
  按 Tier A 封顶。
- 回滚只需把 inspiration profile 的 B prompt 与 v3 config 重新绑定到上一不可变版本；
  不需要修改调用 A 或其他类目。

## 不可破坏约束

- 不得修改或覆盖 `inspiration-a-v3-hard-defect-recall-rev4-20260805`。
- 不得放宽真实 `subject_obscuring_watermark` 的默认 Tier A；豁免必须同时满足结构化
  A 证据与 B 完整性证据，任何不确定均 fail-closed。
- 不得把随手拍软封顶解释为 L5 红线；L5 仍只来自显式红线联合条件。
- 不得修改 90/75/60/0 阈值、基础分、八维 evidence、baseline 真值或其他类目合同。
