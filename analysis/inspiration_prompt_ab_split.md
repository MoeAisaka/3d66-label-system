# 灵感图 Prompt A/B 剥离与 v3 合同重建

## 原始产品 prompt 结构分析

产品提供的单体 prompt 实际混合了：
1. **调用 A 的隐式职责**：红线检测 + 赛道分类 + 媒介判定（这些在 prompt 里是"评审全流程刚性执行顺序"的前置步骤）
2. **调用 B 的显式职责**：8 维度打分 + 4 个 special_checks

## 调用 A：分类与预检（需手动剥离）

### 输入
- 图片

### 输出字段（从 prompt 逆向提取）
```json
{
  "redline_triggered": {
    "screenshot": false,
    "casual_photo": false,
    "text_heavy": false,
    "qr_code_heavy": false
  },
  "track_classification": "class_one",  // class_one | class_two | class_three
  "track_confidence": 0.95,
  "media_type": "real_photo",  // real_photo | 3d_render | ai_generated | other
  "media_confidence": 0.90,
  "primary_category": "建筑设计",  // 17 选 1
  "secondary_category": "商业建筑",  // 自由文本，根据一级分类生成
  "classification_confidence": 0.85
}
```

### Prompt A（需新写）
基于产品 prompt 的"一、红线检测" + "二、赛道分类" + "六、媒介类型" + "智能标签与命名"章节，提炼为纯分类任务 prompt：
- 只输出上述 JSON 结构化字段
- 不涉及维度打分
- 不计算最终分数

---

## 调用 B：维度评估（已有 JSON schema）

### 输入
- 图片
- 调用 A 的分类结果（track_classification, primary_category）

### 输出字段（产品 prompt 已明确）
```json
{
  "dimensions": {
    "composition_viewpoint": {"grade": 1, "evidence": [], "defects": [], "uncertainties": []},
    "lighting_atmosphere": {"grade": 1, "evidence": [], "defects": [], "uncertainties": []},
    "color_material": {"grade": 1, "evidence": [], "defects": [], "uncertainties": []},
    "spatial_design_furnishing": {"grade": 1, "evidence": [], "defects": [], "uncertainties": []},
    "visual_hierarchy": {"grade": 1, "evidence": [], "defects": [], "uncertainties": []},
    "detail_completion": {"grade": 1, "evidence": [], "defects": [], "uncertainties": []},
    "inspiration_reference": {"grade": 1, "evidence": [], "defects": [], "uncertainties": []},
    "presentation_integrity": {"grade": 1, "evidence": [], "defects": [], "uncertainties": []}
  },
  "special_checks": {
    "furniture_soft_furnishing": {"applicable": true, "grade": 1, "evidence": []},
    "rendering_realism": {"applicable": false, "grade": 0, "evidence": []},
    "asset_style_consistency": {"applicable": true, "grade": 1, "evidence": []},
    "image_clarity": {"applicable": true, "grade": 1, "evidence": []}
  },
  "strengths": [],
  "defects": [],
  "assessment_confidence": 0.0,
  "needs_review": false,
  "review_reasons": []
}
```

### Prompt B（产品已提供，需微调）
产品 prompt 的"四、6大核心维度" + "五、5大简易维度"部分，改为：
- 前置上下文：调用 A 已判定 track=XXX, primary_category=XXX
- 只输出 8 维度 grade（1-10/6）+ special_checks
- 移除"红线检测"、"赛道分类"、"媒介扣分"、"80 压分"（这些由确定性引擎负责）

---

## v3 合同重建要点

### 1. subcategory_dimensions
三个赛道（class_one/two/three）都采用统一的 8 维度：
- **common_group**：8 个维度全部作为 common（不再区分 common/specific）
- **specific_group**：空或用于 special_checks

### 2. classification_map
保持不变（17 个一级分类 → 3 个赛道映射已验证对齐）

### 3. redline_policy
四类红线规则已实现，无需改动

### 4. aggregator
需调整：
- 赛道基底分（class_one=100, class_two=80, class_three=70）
- 维度总分池（class_one/two=60, class_three=30）
- 8 维度各自满分与扣分规则（需从 prompt 逆向编码）
- 媒介扣分（3d_render=-5, ai_generated=-15）
- 80 分压分红线（10 条硬伤规则）

---

## 下一步执行计划
1. ✅ 完成 A/B 剥离分析（本文档）
2. ⏳ 新建 `prompts/inspiration_image_call_a.txt`（分类预检 prompt）
3. ⏳ 调整 `prompts/inspiration_image_call_b.txt`（基于产品 prompt，移除分类/算分逻辑）
4. ⏳ 重写 `app/inspiration_category_seed.py` 的 `build_inspiration_subcategory_dimensions()`（8 维度体系）
5. ⏳ 更新 `app/category_evaluation_aggregator.py` 的 inspiration_image 聚合规则（维度扣分表 + 媒介 + 80 压分）
6. ⏳ 三平台验证（Mac/Windows/Linux）+ dry-run 测试
