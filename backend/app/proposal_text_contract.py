from __future__ import annotations
from copy import deepcopy
from math import isfinite
from typing import Any, Mapping

class ProposalTextContractError(ValueError): pass

PROPOSAL_REDLINE_TYPES=("页数不足","竞品水印","内容异常","核心方案缺失","类目不符或禁用类型","内容安全违规")
_TRACK_NAMES={"A","B","C","balanced"}
_LEVEL_ORDER=("L5","L4","L3","L2","L1")
_A_KEYS={"预检结果","材料扫描","红线检查","信息提取","待复核项","置信度"}
_B_KEYS={"scoring_track","visual_score","narrative_score","innovation_timeliness_score","reason","evidence_notes"}

def _fail(msg:str)->None: raise ProposalTextContractError(msg)
def _map(v:object,path:str)->Mapping[str,Any]:
    if not isinstance(v,Mapping): _fail(f"{path}必须是对象")
    return v
def _exact(v:Mapping[str,Any],keys:set[str],path:str)->None:
    if set(v)!=keys: _fail(f"{path}字段集合不合法")
def _text(v:object,path:str)->None:
    if not isinstance(v,str) or not v.strip(): _fail(f"{path}必须是非空字符串")
def _int(v:object)->bool: return isinstance(v,int) and not isinstance(v,bool)
def _version_text(v:object,path:str)->None:
    _text(v,path)
    if len(v)>128 or not v.isprintable(): _fail(f"{path}格式不合法")
def _bounded_int(v:object,path:str,minimum:int,maximum:int)->None:
    if not _int(v) or not minimum<=v<=maximum: _fail(f"{path}必须是{minimum}..{maximum}的整数")
def _bounded_number(v:object,path:str,minimum:float,maximum:float)->None:
    if isinstance(v,bool) or not isinstance(v,(int,float)) or not isfinite(v) or not minimum<=v<=maximum:
        _fail(f"{path}必须是{minimum:g}..{maximum:g}的有限数值")

def _validate_grade_bands(raw:object)->Mapping[str,Any]:
    bands=_map(raw,"grade_bands")
    if bands.get("computed_by")!="engine": _fail("等级必须由引擎计算")
    expected_low=0
    for level in _LEVEL_ORDER:
        interval=bands.get(level)
        if not isinstance(interval,list) or len(interval)!=2:
            _fail(f"{level}区间必须是两个整数的闭区间")
        low,high=interval
        if not _int(low) or not _int(high) or not 0<=low<=high<=100:
            _fail(f"{level}区间不合法")
        if low!=expected_low:
            _fail("等级区间必须连续且完整覆盖0..100")
        expected_low=high+1
    if expected_low!=101:
        _fail("等级区间必须连续且完整覆盖0..100")
    return bands

def validate_proposal_text_contract(contract:Mapping[str,Any])->dict[str,Any]:
    d=_map(contract,"contract")
    fixed={"contract_version":"evaluation-category-profile-v3","profile_type":"text-proposal-additive-v1","category_key":"proposal_text_pdf"}
    for k,v in fixed.items():
        if d.get(k)!=v: _fail(f"{k}不匹配")
    for key in ("display_name","source_standard"):
        _text(d.get(key),key)
    for key in ("spec_version","call_a_version","call_b_version"):
        _version_text(d.get(key),key)
    channel=_map(d.get("pdf_input_channel"),"pdf_input_channel")
    text_layer=_map(channel.get("text_layer"),"pdf_input_channel.text_layer")
    call_a=_map(channel.get("call_a"),"pdf_input_channel.call_a")
    call_b=_map(channel.get("call_b"),"pdf_input_channel.call_b")
    audit=_map(channel.get("audit"),"pdf_input_channel.audit")
    if channel.get("schema_version")!="proposal-pdf-input-v1" or channel.get("long_image_stitching") is not False:
        _fail("PDF输入通道版本或长图禁令不匹配")
    if (text_layer.get("primary"),text_layer.get("extract_all_pages"),text_layer.get("ocr_only_without_text"))!=(True,True,True):
        _fail("PDF文本层策略不匹配")
    if (
        call_a.get("mode"),call_a.get("scan_all_pages"),
        call_a.get("stop_on_redline"),call_a.get("redline_merge"),
    )!=("paged_batches",True,True,"union"):
        _fail("调用A分批策略不匹配")
    _bounded_int(call_a.get("batch_size"),"pdf_input_channel.call_a.batch_size",1,32)
    _bounded_int(call_a.get("max_side_px"),"pdf_input_channel.call_a.max_side_px",512,2048)
    _version_text(call_a.get("information_merge"),"pdf_input_channel.call_a.information_merge")
    if (
        call_b.get("mode"),
        call_b.get("high_fidelity"),call_b.get("model_page_selection"),
    )!=("deterministic_representative_pages",True,False):
        _fail("调用B代表页策略不匹配")
    _bounded_int(call_b.get("sample_size"),"pdf_input_channel.call_b.sample_size",1,32)
    if call_b.get("required_inputs")!=["table_of_contents","text_layer_summary","sampled_pages"]:
        _fail("调用B输入集合不匹配")
    if (
        audit.get("record_page_batches"),audit.get("record_sampled_pages"),
        audit.get("record_tokens_by_stage"),
    )!=(True,True,True):
        _fail("PDF审计策略不匹配")
    r=_map(d.get("redline_policy"),"redline_policy")
    if (r.get("enabled"),r.get("signal"),r.get("hit_level"),r.get("terminal"))!=(True,"precheck.红线检查.命中项[].类型","L5",True): _fail("红线语义不匹配")
    _bounded_number(r.get("hit_score_cap"),"redline_policy.hit_score_cap",0,100)
    rules=r.get("rules")
    if not isinstance(rules,list): _fail("红线规则必须是数组")
    found={x for rule in rules if isinstance(rule,Mapping) for x in rule.get("match_any",[]) if isinstance(x,str)}
    if found!=set(PROPOSAL_REDLINE_TYPES): _fail("红线枚举不匹配")
    manual=_map(d.get("manual_review_policy"),"manual_review_policy")
    if manual.get("behavior")!="fail_closed" or manual.get("grade_output") is not None:
        _fail("人工复核必须保持fail-closed且不自动产出等级")
    tracks=_map(_map(d.get("track_classification"),"track_classification").get("tracks"),"tracks")
    if set(tracks)!=_TRACK_NAMES: _fail("赛道集合不匹配")
    for name in sorted(_TRACK_NAMES):
        t=_map(tracks[name],f"tracks.{name}")
        _text(t.get("display_name"),f"tracks.{name}.display_name")
        members=t.get("members")
        if not isinstance(members,list) or not all(isinstance(item,str) and item.strip() for item in members):
            _fail(f"tracks.{name}.members必须是非空字符串数组")
        if len(set(members))!=len(members): _fail(f"tracks.{name}.members不得重复")
        maxima=[]
        for key in ("visual_max","narrative_max","innovation_max"):
            value=t.get(key);_bounded_int(value,f"tracks.{name}.{key}",0,100);maxima.append(value)
        if sum(maxima)>100: _fail(f"赛道{name}三分项上限之和不得超过100")
    s=_map(d.get("scoring"),"scoring")
    if s.get("mode")!="additive" or s.get("components")!=["visual_score","narrative_score","innovation_timeliness_score"] or s.get("computed_by")!="engine" or s.get("model_must_not_output")!=["score","rate","grade"]: _fail("评分语义不匹配")
    base=_map(d.get("aesthetic_base_score"),"aesthetic_base_score")
    if base.get("field")!="proposal_aesthetic_score" or base.get("immutable") is not True: _fail("基础分语义不匹配")
    _validate_grade_bands(d.get("grade_bands"))
    return deepcopy(dict(d))

def validate_proposal_call_a_output(payload:Mapping[str,Any])->dict[str,Any]:
    d=_map(payload,"调用A");_exact(d,_A_KEYS,"调用A")
    result=_map(d["预检结果"],"预检结果");_exact(result,{"状态","是否进入B","结论说明"},"预检结果")
    state=result.get("状态")
    if state not in {"通过","淘汰","人工复核"} or not isinstance(result.get("是否进入B"),bool): _fail("预检状态不合法")
    _text(result.get("结论说明"),"结论说明")
    scan=_map(d["材料扫描"],"材料扫描");_exact(scan,{"文件列表","文件格式","总页数","页面可读性"},"材料扫描")
    for k in ("文件列表","文件格式"):
        if not isinstance(scan.get(k),list) or not all(isinstance(x,str) and x for x in scan[k]): _fail(f"{k}不合法")
    pages=scan.get("总页数")
    if pages is not None and (not _int(pages) or pages<0): _fail("总页数不合法")
    if scan.get("页面可读性") not in {"正常","部分异常","无法判断"}: _fail("页面可读性不合法")
    red=_map(d["红线检查"],"红线检查");_exact(red,{"是否命中","命中项"},"红线检查")
    hit=red.get("是否命中");items=red.get("命中项")
    if hit is not True and hit is not False and hit is not None: _fail("红线命中值不合法")
    if not isinstance(items,list): _fail("红线命中项不合法")
    for raw in items:
        item=_map(raw,"红线命中项");_exact(item,{"类型","说明","证据"},"红线命中项")
        if item.get("类型") not in PROPOSAL_REDLINE_TYPES: _fail("红线枚举越界")
        _text(item.get("说明"),"红线说明")
        if not isinstance(item.get("证据"),list): _fail("红线证据不合法")
        for raw_ev in item["证据"]:
            ev=_map(raw_ev,"红线证据");_exact(ev,{"source","page","observation"},"红线证据")
            _text(ev.get("source"),"证据来源");_text(ev.get("observation"),"证据描述")
            if ev.get("page") is not None and (not _int(ev["page"]) or ev["page"]<1): _fail("证据页码不合法")
    if not isinstance(d.get("待复核项"),list) or not all(isinstance(x,str) for x in d["待复核项"]): _fail("待复核项不合法")
    conf=d.get("置信度")
    if isinstance(conf,bool) or not isinstance(conf,(int,float)) or not 0<=conf<=1: _fail("置信度不合法")
    enter=result["是否进入B"];info=d.get("信息提取")
    if state=="通过":
        if enter is not True or hit is not False or items or not isinstance(info,Mapping): _fail("通过状态不一致")
        _text(_map(info.get("项目分类"),"项目分类").get("审核类别"),"审核类别")
    elif state=="淘汰":
        if enter is not False or hit is not True or not items or info is not None: _fail("淘汰状态不一致")
    elif enter is not False or hit is not None or items or info is not None: _fail("人工复核状态不一致")
    return deepcopy(dict(d))

def _expected(category:str,tracks:Mapping[str,Any])->str|None:
    if category in {"A","B","C"}: return category
    for name in ("A","B","C"):
        if category in tracks[name].get("members",[]): return name
    return None

def validate_proposal_call_b_output(payload:Mapping[str,Any],*,contract:Mapping[str,Any],audit_category:str)->dict[str,Any]:
    cfg=validate_proposal_text_contract(contract);d=_map(payload,"调用B");_exact(d,_B_KEYS,"调用B")
    tracks=cfg["track_classification"]["tracks"];track=d.get("scoring_track")
    if track not in tracks: _fail("scoring_track越界")
    expected=_expected(audit_category,tracks)
    if audit_category!="其他" and expected is None: _fail("审核类别无法映射")
    if expected is not None and track!=expected: _fail("赛道与审核类别不一致")
    bounds={"visual_score":tracks[track]["visual_max"],"narrative_score":tracks[track]["narrative_max"],"innovation_timeliness_score":tracks[track]["innovation_max"]}
    for k,maximum in bounds.items():
        value=d.get(k)
        if not _int(value) or not 0<=value<=maximum: _fail(f"{k}越界")
    _text(d.get("reason"),"reason");notes=d.get("evidence_notes")
    if not isinstance(notes,list) or not 1<=len(notes)<=4 or not all(isinstance(x,str) and x.strip() for x in notes): _fail("evidence_notes不合法")
    return deepcopy(dict(d))
