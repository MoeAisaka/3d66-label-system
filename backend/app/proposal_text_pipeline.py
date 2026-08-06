from __future__ import annotations
from dataclasses import dataclass
from typing import Any,Awaitable,Callable

@dataclass(frozen=True)
class ValidatedStage:
    value: dict[str,Any]|None
    responses: tuple[Any,...]
    error: str|None

async def call_validated_json(invoke:Callable[[],Awaitable[Any]],validator:Callable[[Any],dict[str,Any]])->ValidatedStage:
    responses:list[Any]=[];last_error:str|None=None
    for _ in range(2):
        response=await invoke();responses.append(response)
        try:return ValidatedStage(validator(response.parsed),tuple(responses),None)
        except (AttributeError,TypeError,ValueError) as exc:last_error=str(exc)
    return ValidatedStage(None,tuple(responses),last_error or "输出校验失败")

def build_deterministic_page_precheck(filename:str,page_count:int|None)->dict[str,Any]|None:
    if page_count is None or page_count>=15:return None
    return {
        "预检结果":{"状态":"淘汰","是否进入B":False,"结论说明":f"PDF元数据总页数为{page_count}，少于15页"},
        "材料扫描":{"文件列表":[filename],"文件格式":["PDF"],"总页数":page_count,"页面可读性":"正常"},
        "红线检查":{"是否命中":True,"命中项":[{"类型":"页数不足","说明":f"总页数{page_count}少于15页","证据":[{"source":filename,"page":None,"observation":f"PDF元数据页数={page_count}"}]}]},
        "信息提取":None,"待复核项":[],"置信度":1.0,
    }

def build_manual_review_precheck(filename:str,page_count:int|None,reason:str)->dict[str,Any]:
    return {
        "预检结果":{"状态":"人工复核","是否进入B":False,"结论说明":reason},
        "材料扫描":{"文件列表":[filename],"文件格式":["PDF"],"总页数":page_count,"页面可读性":"无法判断"},
        "红线检查":{"是否命中":None,"命中项":[]},"信息提取":None,
        "待复核项":[reason],"置信度":0.0,
    }
