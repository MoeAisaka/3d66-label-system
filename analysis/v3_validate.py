"""V3 引擎评分校验：用人工分析的 调用A+调用B 结果跑确定性引擎，对比人工评级。

调用A/B 由 OpenClaw 视觉模型按产品 rubric 逐图产出（见 RECORDS），本脚本只跑
inspiration_image v3 确定性引擎（红线→赛道→维度扣分→媒介→80压分→level），
再把预测 level/score 与文件名里的人工 ground-truth 对比。
"""
from __future__ import annotations
import json
from app.inspiration_category_seed import (
    build_inspiration_v3_contract,
    build_inspiration_classification_map,
    build_inspiration_subcategory_dimensions,
    evaluate_one,
)

# 人工评级 → 期望 level 语义带（doc-l5-worst: L1最优…L5最差）
# 好=L1/L2区间, 中等=L3附近, 中差=L3/L4, 极差=L4/L5, 过滤(红线)=L5
GROUND = {
    "好": "high",      # 期望高分 L1-L2
    "中等": "mid",     # 期望中分 L2-L3
    "中差": "midlow",  # 期望偏低 L3-L4
    "极差": "low",     # 期望低分 L4-L5
    "过滤": "reject",  # 期望红线 L5/hard_reject
}

MEDIA_MAP = {"real_photo": "real_photo", "3d_render": "render_3d",
             "ai_generated": "ai_image", "other": "other"}

# 红线原因映射：调用A 的 redline 布尔 → 引擎 reason 枚举
def redline_reasons(rec):
    r = []
    for d in rec.get("hard_defects", []):
        pass
    # 本校验里 redline=true 且属于四类红线才算；用 hard_defects/文件名无法精确区分，
    # 采用调用A 显式 redline 标志 + 具体命中类型（此处简化：redline=true 记为截图类占位）
    if rec.get("_redline_type"):
        r.append(rec["_redline_type"])
    return r

# 每条记录：idx, 人工label, 调用A/B 输出。redline_type 为命中的具体红线原因(供引擎匹配)。
RECORDS = [
  {"idx":1,"label":"好","redline":False,"track":"class_one","media":"real_photo","primary":"建筑设计","hard_defects":[],"dims":{"visual_structure":5,"color_aesthetics":4,"emotional_expression":4,"design_aesthetics":5,"originality":5,"design_trendiness":4}},
  {"idx":2,"label":"好","redline":False,"track":"class_one","media":"real_photo","primary":"居住空间","hard_defects":["大面积死黑"],"dims":{"visual_structure":4,"color_aesthetics":4,"emotional_expression":4,"design_aesthetics":4,"originality":3,"design_trendiness":4}},
  {"idx":3,"label":"好","redline":False,"track":"class_one","media":"real_photo","primary":"公共空间","hard_defects":[],"dims":{"visual_structure":5,"color_aesthetics":4,"emotional_expression":5,"design_aesthetics":5,"originality":5,"design_trendiness":5}},
  {"idx":4,"label":"好","redline":False,"track":"class_one","media":"real_photo","primary":"公共空间","hard_defects":[],"dims":{"visual_structure":5,"color_aesthetics":4,"emotional_expression":4,"design_aesthetics":5,"originality":5,"design_trendiness":5}},
  {"idx":5,"label":"好","redline":False,"track":"class_one","media":"3d_render","primary":"景观设计","hard_defects":["材质虚假"],"dims":{"visual_structure":4,"color_aesthetics":4,"emotional_expression":4,"design_aesthetics":4,"originality":4,"design_trendiness":5}},
  {"idx":6,"label":"好","redline":False,"track":"class_one","media":"real_photo","primary":"居住空间","hard_defects":[],"dims":{"visual_structure":5,"color_aesthetics":4,"emotional_expression":4,"design_aesthetics":5,"originality":4,"design_trendiness":5}},
  {"idx":7,"label":"中等","redline":False,"track":"class_one","media":"real_photo","primary":"公共空间","hard_defects":["模糊发灰"],"dims":{"visual_structure":4,"color_aesthetics":3,"emotional_expression":3,"design_aesthetics":4,"originality":3,"design_trendiness":3}},
  {"idx":8,"label":"中等","redline":False,"track":"class_one","media":"real_photo","primary":"景观设计","hard_defects":["构图敷衍"],"dims":{"visual_structure":3,"color_aesthetics":3,"emotional_expression":4,"design_aesthetics":4,"originality":4,"design_trendiness":4}},
  {"idx":9,"label":"中等","redline":False,"track":"class_one","media":"3d_render","primary":"居住空间","hard_defects":["材质虚假"],"dims":{"visual_structure":4,"color_aesthetics":4,"emotional_expression":3,"design_aesthetics":4,"originality":3,"design_trendiness":4}},
  {"idx":10,"label":"中等","redline":False,"track":"class_one","media":"3d_render","primary":"建筑设计","hard_defects":["材质虚假"],"_redline_type":"有大面积文字说明","redline":True,"dims":{"visual_structure":3,"color_aesthetics":3,"emotional_expression":2,"design_aesthetics":3,"originality":2,"design_trendiness":3}},
  {"idx":11,"label":"中差","redline":False,"track":"class_one","media":"3d_render","primary":"居住空间","hard_defects":[],"dims":{"visual_structure":4,"color_aesthetics":4,"emotional_expression":3,"design_aesthetics":4,"originality":3,"design_trendiness":4}},
  {"idx":12,"label":"中差","redline":False,"track":"class_one","media":"3d_render","primary":"展示设计","hard_defects":["大面积死黑"],"dims":{"visual_structure":4,"color_aesthetics":3,"emotional_expression":4,"design_aesthetics":4,"originality":3,"design_trendiness":4}},
  {"idx":13,"label":"中差","redline":True,"_redline_type":"是截图","track":"class_one","media":"3d_render","primary":"办公空间","hard_defects":[],"dims":{"visual_structure":4,"color_aesthetics":4,"emotional_expression":3,"design_aesthetics":4,"originality":3,"design_trendiness":4}},
  {"idx":14,"label":"中差","redline":False,"track":"class_one","media":"3d_render","primary":"商业空间","hard_defects":[],"dims":{"visual_structure":4,"color_aesthetics":4,"emotional_expression":4,"design_aesthetics":4,"originality":4,"design_trendiness":4}},
  {"idx":15,"label":"极差","redline":True,"_redline_type":"是随手拍","track":"class_three","media":"real_photo","primary":"其它","hard_defects":["无效黑边"],"dims":{"subject_focus":4,"mood_atmosphere":3,"composition_lighting":3,"reference_value":2,"visual_impact":3}},
  {"idx":16,"label":"极差","redline":True,"_redline_type":"是随手拍","track":"class_three","media":"real_photo","primary":"视觉设计","hard_defects":["构图敷衍"],"dims":{"subject_focus":2,"mood_atmosphere":3,"composition_lighting":2,"reference_value":2,"visual_impact":2}},
  {"idx":17,"label":"极差","redline":True,"_redline_type":"有大面积文字说明","track":"class_two","media":"other","primary":"美术类","hard_defects":[],"dims":{"visual_structure":4,"color_aesthetics":4,"emotional_expression":4,"design_aesthetics":4,"originality":4,"design_trendiness":4}},
  {"idx":18,"label":"过滤","redline":True,"_redline_type":"是截图","track":"class_three","media":"real_photo","primary":"意向图","hard_defects":["无效黑边"],"dims":{"subject_focus":4,"mood_atmosphere":4,"composition_lighting":4,"reference_value":3,"visual_impact":4}},
  {"idx":19,"label":"过滤","redline":True,"_redline_type":"是截图","track":"class_three","media":"real_photo","primary":"意向图","hard_defects":["无效黑边"],"dims":{"subject_focus":4,"mood_atmosphere":4,"composition_lighting":4,"reference_value":3,"visual_impact":4}},
  {"idx":20,"label":"过滤","redline":True,"_redline_type":"有大面积文字说明","track":"class_two","media":"other","primary":"游戏设计","hard_defects":["大面积文字"],"dims":{"visual_structure":3,"color_aesthetics":3,"emotional_expression":3,"design_aesthetics":3,"originality":3,"design_trendiness":3}},
]

def build_precheck(rec):
    reason = []
    if rec.get("redline") and rec.get("_redline_type"):
        reason.append(rec["_redline_type"])
    # hard_defects → 引擎 80压分信号（用产品硬伤枚举原文即可，聚合器只看非空）
    hard = list(rec.get("hard_defects", []))
    return {
        "classification": {
            "primary_category": rec["primary"],
            "scope_status": "in_scope",
            "primary_confidence": 0.9,
        },
        "production_fields": {"reason": reason, "trait": rec["media"]},
        "media_type": MEDIA_MAP.get(rec["media"], "other"),
        "hard_defects": hard,
    }

def main():
    c = build_inspiration_v3_contract()
    m = build_inspiration_classification_map()
    d = build_inspiration_subcategory_dimensions()
    print(f"{'idx':>3} {'人工':<5} {'track':<12} {'media':<11} {'score':>5} {'level':<4} {'reject':<6} note")
    rows = []
    for rec in RECORDS:
        pre = build_precheck(rec)
        tk = rec["track"]
        r = evaluate_one(
            contract=c, classification_map=m, subcategory_dimensions=d,
            precheck=pre,
            common_grades_by_track={tk: rec["dims"]},
            specific_grades_by_track={},
        )
        res = r["result"]
        rows.append({"idx":rec["idx"],"label":rec["label"],"track":res.get("track_key"),
                     "score":res["score"],"level":res["level"],"hard_reject":res["hard_reject"]})
        print(f"{rec['idx']:>3} {rec['label']:<5} {str(res.get('track_key')):<12} {pre['media_type']:<11} "
              f"{str(res['score']):>5} {res['level']:<4} {str(res['hard_reject']):<6}")
    # 保存 JSON
    with open("analysis/v3_validate_result.json","w",encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
