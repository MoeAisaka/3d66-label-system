from __future__ import annotations
from copy import deepcopy
from typing import Any,Mapping
from app.proposal_text_contract import ProposalTextContractError,validate_proposal_call_a_output,validate_proposal_call_b_output,validate_proposal_text_contract

class ProposalTextAggregationError(ValueError): pass

def _grade(score:int,bands:Mapping[str,list[int]])->str:
    for level in ("L1","L2","L3","L4","L5"):
        low,high=bands[level]
        if low<=score<=high:return level
    raise ProposalTextAggregationError(f"总分{score}不在等级区间内")

def _empty()->dict[str,Any]:
    return {"engine_version":"proposal-text-additive-engine-v1","status":None,"needs_review":False,"hard_reject":False,"proposal_aesthetic_score":None,"visual_score":None,"narrative_score":None,"innovation_timeliness_score":None,"score":None,"level":None,"grade":None,"redline_hits":[],"scoring_track":None,"reason":None,"evidence_notes":[]}

def aggregate_proposal_text_evaluation(contract:Mapping[str,Any],precheck:Mapping[str,Any],scoring:Mapping[str,Any]|None)->dict[str,Any]:
    try:cfg=validate_proposal_text_contract(contract);call_a=validate_proposal_call_a_output(precheck)
    except ProposalTextContractError as exc:raise ProposalTextAggregationError(str(exc)) from exc
    result=_empty();status=call_a["预检结果"]["状态"];result["status"]=status
    if status=="人工复核":
        result["needs_review"]=True;result["reason"]=call_a["预检结果"]["结论说明"];return result
    if status=="淘汰":
        level=cfg["redline_policy"]["hit_level"]
        result.update(hard_reject=True,score=cfg["redline_policy"]["hit_score_cap"],level=level,grade=level,redline_hits=[x["类型"] for x in call_a["红线检查"]["命中项"]],reason=call_a["预检结果"]["结论说明"])
        return result
    if scoring is None:raise ProposalTextAggregationError("调用A通过后必须提供有效调用B输出")
    category=call_a["信息提取"]["项目分类"]["审核类别"]
    try:b=validate_proposal_call_b_output(scoring,contract=cfg,audit_category=category)
    except ProposalTextContractError as exc:raise ProposalTextAggregationError(str(exc)) from exc
    base=b["visual_score"]+b["narrative_score"]+b["innovation_timeliness_score"];level=_grade(base,cfg["grade_bands"])
    result.update(proposal_aesthetic_score=base,visual_score=b["visual_score"],narrative_score=b["narrative_score"],innovation_timeliness_score=b["innovation_timeliness_score"],score=base,level=level,grade=level,scoring_track=b["scoring_track"],reason=b["reason"],evidence_notes=deepcopy(b["evidence_notes"]))
    return result
