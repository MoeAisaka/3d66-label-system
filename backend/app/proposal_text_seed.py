from __future__ import annotations
import hashlib
import json
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session
from .category_pipeline import validate_pipeline_config
from .dimension_schema_registry import canonical_json
from .models import CategoryEvaluationV3Config,EvaluationCategoryProfile,ModelConfig,PromptVersion
from .proposal_text_contract import validate_proposal_text_contract

PROPOSAL_CATEGORY_KEY="proposal_text_pdf"
PROPOSAL_SPEC_VERSION="proposal-text-v1-baseline-20260806"
PROPOSAL_CALL_A_VERSION="proposal-text-a-v1-baseline-20260806"
PROPOSAL_CALL_B_VERSION="proposal-text-b-v1-baseline-20260806"
_ASSET_DIR=Path(__file__).with_name("proposal_text_assets")

def _read(name:str)->str:return (_ASSET_DIR/name).read_text(encoding="utf-8")

def proposal_text_pipeline()->dict:
    return validate_pipeline_config({
        "schema_version":"category-pipeline-v1","input_kind":"pdf","allowed_suffixes":[".pdf"],
        "processors":[
            {"module":"document.pdf_extract","enabled":True,"config":{"max_pages":20,"max_text_chars":100000}},
            {"module":"document.ocr_if_needed","enabled":True,"config":{"min_text_chars":80}},
            {"module":"document.page_contact_sheet","enabled":True,"config":{}},
        ],
        "prompt_mode":"follow","prompt_context":{"instruction":""},
        "dimensions":{"enabled":False,"mode":"none","enabled_keys":[]},
        "model_nodes":{"evaluation_main":True,"pdf_summary":False,"optimization":True,"benchmark":True,"diagnostic":True},
    })

def _prompt(db:Session,stage:str,version:str,text:str)->PromptVersion:
    existing=db.scalar(select(PromptVersion).where(PromptVersion.version==version))
    if existing is not None:
        if existing.category_key!=PROPOSAL_CATEGORY_KEY or existing.stage!=stage or existing.system_prompt!=text or existing.user_prompt!="":
            raise RuntimeError(f"冻结提示词{version}已存在但内容或身份不匹配")
        return existing
    row=PromptVersion(stage=stage,category_key=PROPOSAL_CATEGORY_KEY,pipeline_scope="shared",name=f"PDF方案文本调用{stage}",version=version,system_prompt=text,user_prompt="",rubric_version=PROPOSAL_SPEC_VERSION,status="published",source="imported",change_note="2026-08-06 PDF方案文本二期正式接入，逐字冻结交付提示词",created_by="system:proposal-text-v1")
    db.add(row);db.flush();return row

def seed_proposal_text_pdf(db:Session)->None:
    contract=json.loads(_read("v3_contract_proposal_text_v1.json"));validate_proposal_text_contract(contract)
    prompt_a=_prompt(db,"A",PROPOSAL_CALL_A_VERSION,_read("call_a_proposal_text_v1.txt"))
    prompt_b=_prompt(db,"B",PROPOSAL_CALL_B_VERSION,_read("call_b_proposal_text_v1.txt"))
    primary=db.scalar(select(ModelConfig).where(ModelConfig.active.is_(True)).order_by(ModelConfig.id))
    if primary is None:raise RuntimeError("缺少 active 评测模型，无法启用PDF方案文本类目")
    pipeline=proposal_text_pipeline()
    profile=db.scalar(select(EvaluationCategoryProfile).where(EvaluationCategoryProfile.category_key==PROPOSAL_CATEGORY_KEY))
    values={"display_name":"PDF方案文本","description":"text-proposal-additive-v1：PDF预处理、A预检、B三分项、引擎定级","status":"active","allowed_mime_types_json":'["application/pdf"]',"preprocess_config_json":canonical_json({"preprocess":"pdf","max_pages":20,"max_text_chars":100000,"multimodal_summary":False}),"pipeline_config_json":canonical_json(pipeline),"prompt_a_id":prompt_a.id,"prompt_b_id":prompt_b.id,"model_config_id":primary.id,"rubric_version":PROPOSAL_SPEC_VERSION,"dimension_schema_key":None,"dimension_schema_version":None,"created_by":"system:proposal-text-v1"}
    if profile is None:
        profile=EvaluationCategoryProfile(category_key=PROPOSAL_CATEGORY_KEY,pipeline_revision=1,**values);db.add(profile)
    elif profile.rubric_version!=PROPOSAL_SPEC_VERSION:
        raise RuntimeError("proposal_text_pdf已存在非本版本配置，拒绝启动时覆盖")
    classification={"profile_type":"text-proposal-additive-v1","source":"precheck.信息提取.项目分类.审核类别"}
    dimensions={"profile_type":"text-proposal-additive-v1","tracks":["A","B","C","balanced"]}
    contract_json=canonical_json(contract);contract_hash=hashlib.sha256(contract_json.encode()).hexdigest()
    row=db.scalar(select(CategoryEvaluationV3Config).where(CategoryEvaluationV3Config.category_key==PROPOSAL_CATEGORY_KEY))
    if row is None:
        db.add(CategoryEvaluationV3Config(category_key=PROPOSAL_CATEGORY_KEY,display_name="PDF方案文本",status="active",contract_json=contract_json,classification_map_json=canonical_json(classification),subcategory_dimensions_json=canonical_json(dimensions),dimension_deduction_rules_json="{}",media_penalty_enabled=False,revision=1,contract_hash=contract_hash,created_by="system:proposal-text-v1"))
    else:
        current=json.loads(row.contract_json or "{}")
        if current.get("spec_version")!=PROPOSAL_SPEC_VERSION or row.contract_json!=contract_json:
            raise RuntimeError("proposal_text_pdf v3合同已存在冲突版本，拒绝覆盖")
