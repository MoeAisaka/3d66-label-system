# ADR-0033 Task 3b 规格冻结：inspiration_image 真实合同（方案 A，6/5 维度）

> 依据：产品《【灵感图】-prompt》文档 + Owner 2026-08-04 决定「方案 A：以算分章节为准」。
> 目标：用产品真实的 6/5 维度体系重建 inspiration_image 的 v3 合同，A/B 剥离，
> 确定性引擎（红线/赛道/扣分/媒介/80压分）全部复用，不改引擎核心。

## 一、引擎已验证对齐（不改）
- 四红线 → 命中 L5、hard_reject、score≤49 ✓
- 三赛道基底：class_one 40+60=100、class_two 20+60=80、class_three 40+30=70 ✓
- 媒介扣分：real_photo 0 / 3d_render -5 / ai_generated -15 ✓
- **80分一票压分是条件性的**：`score>=80 且 precheck.hard_defects 非空 → 压至79`（聚合器 Step 6 已实现）✓
- score→level：doc-l5-worst（L5最差、L1最优；score 0-100 越高越好）✓

## 二、维度体系（方案 A，替换框架期占位维度）

### class_one / class_two（6 维度，dimension_max=60）
| key | label | 满分 | weight(=满分/60) |
|-----|-------|------|------|
| visual_structure | 视觉结构 | 10 | 0.166667 |
| color_aesthetics | 色彩美学 | 10 | 0.166667 |
| emotional_expression | 情感表达 | 5 | 0.083333 |
| design_aesthetics | 设计美学 | 10 | 0.166667 |
| originality | 原创设计感 | 10 | 0.166667 |
| design_trendiness | 设计流行度 | 15 | 0.250000 |

权重和=1.0（末位吸收浮点漂移）。

### class_three（5 维度，dimension_max=30，每维满分6）
| key | label | 满分 | weight(=6/30) |
|-----|-------|------|------|
| subject_focus | 主题清晰焦点 | 6 | 0.2 |
| mood_atmosphere | 情绪氛围可感知 | 6 | 0.2 |
| composition_lighting | 构图光影专业章法 | 6 | 0.2 |
| reference_value | 内容借鉴价值 | 6 | 0.2 |
| visual_impact | 强视觉冲击力 | 6 | 0.2 |

### 结构决定
- 每赛道全部维度放入 **common_group（group_weight=1.0）**，**specific_group 置空**（引擎支持空组）。
- grade_points 用线性锚点 `{1:0, 2:25, 3:50, 4:75, 5:100}`：grade5→满分不扣、grade1→全扣该维度 share。
- 调用B 对每个维度打 1-5 grade（产品的"扣3~5分"规则转成 1-5 档评级 rubric 写进 B prompt）。

## 三、调用A 输出字段（必须显式化，产品文档此段为空）
调用A 是预检，产出以下结构化字段（喂给确定性引擎）：
- `reason`：命中的红线原因（枚举，供 redline_policy 匹配：是截图/是随手拍/有大面积文字说明/有二维码）；无红线则空/正常值
- `classification`: `{primary_category(17选1), scope_status, primary_confidence}`
- `media_type`: real_photo / 3d_render / ai_generated / other
- `secondary_category`: 自由文本
- **`hard_defects`: 数组**——命中"80分一票压分"10 条硬伤中的哪些（模糊发灰/构图敷衍/色彩艳俗/大面积死黑/视角畸变/材质虚假/鱼眼畸变/无效黑边/严重偏色/知名实拍硬伤）。**这是 80压分触发的必需信号。**

## 四、special_checks（4项）定位决定
产品的 60/30 分已被 6/5 维度完全占满，special_checks 不再有分数空间。
**决定：special_checks 不参与算分**，仅作为调用B 的记录/复核信号（furniture_soft_furnishing / rendering_realism / asset_style_consistency / image_clarity），存入 result 供人工参考，不影响 score。

## 五、实现范围（Task 3b）
1. 重写 `build_inspiration_subcategory_dimensions()`：输出上述 6/5 真实维度（common_group 满，specific 空）。
2. 更新 `_specific_group`/`_SPECIFIC_DIMENSIONS` 相关：改为单 common_group 承载全部维度。
3. 落地 A/B prompt 到 `backend/prompts/inspiration_image_call_a.txt` / `_call_b.txt`（已起草，按本规格校订：A 加 hard_defects；B 用 6/5 维度 + 1-5 档 rubric）。
4. seed_defaults：inspiration_image 的 A/B PromptVersion 入库（category_key=inspiration_image, stage A/B, status=published）。
5. 更新受影响的框架层测试（原占位维度断言 → 新 6/5 维度）。
6. 不改引擎核心（redline/aggregator/grade bridge/composition）。

## 六、验证
- backend 全量 pytest 三平台（Mac/Windows/Docker）
- 端到端：一类高质量图（全 grade5, real_photo, 无硬伤）→ score 高、L1；一类有硬伤且 ≥80 → 压 79；红线 → L5/≤49；二类 cap 80；三类 cap 70。
- 老类目逐字节不变。
