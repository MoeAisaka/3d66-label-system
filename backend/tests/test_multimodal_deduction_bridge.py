from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib

import pytest

from app.dimension_deduction_bridge import (
    BONUS_CAP_BRIDGE_VERSION,
    DEDUCTION_PROMPT_TEMPLATE_VERSION,
    DimensionDeductionBridgeError,
    FALLBACK_WARNING,
    OPERATOR_PROMPT_TEMPLATE_VERSION,
    CONFLICTING_OUTPUT_CONTRACT_MARKERS,
    SUPPORTED_PLACEHOLDERS,
    call_multimodal_for_dimension_deductions,
    compose_rule_deductions,
    empty_deduction_output,
    foundation_required,
    normalize_dimension_deduction_output,
    operator_prompt_conflicting_contract,
    operator_prompt_declares_rule_takeover,
)
from app.b_aesthetic_foundation import BAestheticFoundationError
from app.inspiration_category_seed import build_inspiration_subcategory_dimensions


class Response:
    def __init__(self, parsed: dict) -> None:
        self.parsed = parsed
        self.raw_payload = {"provider": parsed}


class Client:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.system = ""
        self.user = ""

    async def chat_json(self, system: str, user: str, **_kwargs) -> Response:
        self.system, self.user = system, user
        if self.fail:
            raise TimeoutError("timeout")
        config = build_inspiration_subcategory_dimensions()["class_one"]
        dimensions = []
        for dimension in config["common_group"]["schema_definition"]["dimensions"]:
            hits = []
            if not dimensions:
                rule = dimension["deduction_rules"][0]
                hits = [{"rule_id": rule["rule_id"], "confidence": "high", "evidence": "图中主体偏移"}]
            dimensions.append({"dimension_key": dimension["key"], "hit_rules": hits})
        return Response(
            {
                "aesthetic_score": 88,
                "aesthetic_evidence": ["整体画面基础美感可见"],
                "aesthetic_confidence": 0.8,
                "dimensions": dimensions,
                "overall_note": "已逐条核验",
            }
        )


def _bonus_cap_config() -> dict:
    config = deepcopy(build_inspiration_subcategory_dimensions()["class_one"])
    dimensions = config["common_group"]["schema_definition"]["dimensions"]
    for dimension in dimensions:
        dimension["dimension_score_cap"] = 100
        dimension["bonus_rules"] = []
    dimensions[0]["bonus_rules"] = [
        {
            "rule_id": "composition_clear",
            "description": "构图层级清晰完整",
            "bonus": 8,
            "tags": ["构图"],
        }
    ]
    return config


class BonusCapClient:
    def __init__(self, config: dict, *, fail: bool = False) -> None:
        self.config = config
        self.fail = fail
        self.system = ""
        self.user = ""

    async def chat_json(self, system: str, user: str, **_kwargs) -> Response:
        self.system, self.user = system, user
        if self.fail:
            raise TimeoutError("timeout")
        dimensions = {}
        for index, dimension in enumerate(
            self.config["common_group"]["schema_definition"]["dimensions"]
        ):
            dimensions[dimension["key"]] = {
                "hit_rules": [],
                "hit_bonus_rules": (
                    [
                        {
                            "rule_id": "composition_clear",
                            "confidence": "high",
                            "evidence": "构图层级清晰",
                        }
                    ]
                    if index == 0
                    else []
                ),
            }
        return Response(
            {
                "aesthetic_score": 88,
                "aesthetic_evidence": ["整体画面基础美感可见"],
                "aesthetic_confidence": 0.8,
                "dimensions": dimensions,
                "overall_note": "已核验正负规则",
            }
        )


def test_bridge_normalizes_rule_hits_and_uses_chinese_prompt() -> None:
    config = build_inspiration_subcategory_dimensions()["class_one"]
    client = Client()
    output = asyncio.run(
        call_multimodal_for_dimension_deductions(
            "image.jpg", config, client=client, mime_type="image/jpeg", precheck={}
        )
    )
    assert output["warning"] is None
    first = next(iter(output["dimensions"].values()))
    assert first["hit_rules"][0]["evidence"] == "图中主体偏移"
    assert "aesthetic_score" in client.system
    assert "扣" in client.user
    assert output["prompt_identity"] == {
        "template_version": DEDUCTION_PROMPT_TEMPLATE_VERSION,
        "operator_prompt_version": None,
        "bypassed_operator_prompt_version": None,
        "system_sha256": hashlib.sha256(client.system.encode("utf-8")).hexdigest(),
        "user_sha256": hashlib.sha256(client.user.encode("utf-8")).hexdigest(),
    }


def test_bridge_provider_failure_fails_closed_when_foundation_required() -> None:
    """运营需求：调用B输出为空不许瞎给分——失败兜底会凭空给满分。"""
    config = build_inspiration_subcategory_dimensions()["class_one"]
    with pytest.raises(DimensionDeductionBridgeError) as excinfo:
        asyncio.run(
            call_multimodal_for_dimension_deductions(
                "image.jpg", config, client=Client(fail=True), mime_type="image/jpeg"
            )
        )
    assert excinfo.value.code == "call_b_unavailable"
    assert "不出分" in str(excinfo.value)
    # provider 故障保留可重试语义；合同形状错误没有这两个属性。
    assert excinfo.value.technical_error_type == "network"
    assert excinfo.value.retryable is True


def test_bridge_provider_failure_stays_fail_open_when_foundation_opted_out() -> None:
    """显式 opt-out 美感基础分的旧扣分合同维持已批准的 fail-open。"""
    config = dict(build_inspiration_subcategory_dimensions()["class_one"])
    config["b_aesthetic_foundation"] = {"enabled": False}
    output = asyncio.run(
        call_multimodal_for_dimension_deductions(
            "image.jpg", config, client=Client(fail=True), mime_type="image/jpeg"
        )
    )
    assert output["warning"] == FALLBACK_WARNING
    assert all(item["hit_rules"] == [] for item in output["dimensions"].values())
    identity = output["prompt_identity"]
    assert identity["template_version"] == DEDUCTION_PROMPT_TEMPLATE_VERSION
    assert len(identity["system_sha256"]) == 64
    assert len(identity["user_sha256"]) == 64


def test_bonus_cap_bridge_normalizes_both_rule_arrays_and_prompt() -> None:
    config = _bonus_cap_config()
    client = BonusCapClient(config)
    output = asyncio.run(
        call_multimodal_for_dimension_deductions(
            "image.jpg", config, client=client, mime_type="image/jpeg"
        )
    )
    assert output["bridge_version"] == BONUS_CAP_BRIDGE_VERSION
    first = next(iter(output["dimensions"].values()))
    assert first["hit_rules"] == []
    assert first["hit_bonus_rules"][0]["rule_id"] == "composition_clear"
    assert "扣分规则" in client.user
    assert "加分规则" in client.user


def test_bonus_cap_provider_failure_fails_closed() -> None:
    """bonus-cap 默认承担美感基础分：调用B失败必须 fail-closed 不出分。"""
    config = _bonus_cap_config()
    with pytest.raises(DimensionDeductionBridgeError) as excinfo:
        asyncio.run(
            call_multimodal_for_dimension_deductions(
                "image.jpg",
                config,
                client=BonusCapClient(config, fail=True),
                mime_type="image/jpeg",
            )
        )
    assert excinfo.value.code == "call_b_unavailable"


def test_bonus_cap_bridge_rejects_duplicate_provider_hits() -> None:
    config = _bonus_cap_config()
    first = config["common_group"]["schema_definition"]["dimensions"][0]
    payload = {
        "aesthetic_score": 88,
        "aesthetic_evidence": ["整体画面基础美感可见"],
        "aesthetic_confidence": 0.8,
        "dimensions": {
            dimension["key"]: {"hit_rules": [], "hit_bonus_rules": []}
            for dimension in config["common_group"]["schema_definition"]["dimensions"]
        }
    }
    hit = {
        "rule_id": first["bonus_rules"][0]["rule_id"],
        "confidence": "high",
        "evidence": "同一规则重复返回",
    }
    payload["dimensions"][first["key"]]["hit_bonus_rules"] = [hit, hit]
    with pytest.raises(DimensionDeductionBridgeError) as excinfo:
        normalize_dimension_deduction_output(payload, config)
    assert excinfo.value.code == "rule_duplicate"


# --- Call-B failure must stay fail-open for contracts that never declared the
# --- foundation, while "B answered but omitted the score" stays fail-closed.


def _undeclared_config() -> dict:
    config = deepcopy(build_inspiration_subcategory_dimensions()["class_one"])
    config.pop("b_aesthetic_foundation", None)
    return config


def _answered_without_score(config: dict) -> dict:
    """A payload the provider really returned, minus any aesthetic score."""
    return {
        "dimensions": {
            key: {"hit_rules": []}
            for key in empty_deduction_output(config)["dimensions"]
        },
        "overall_note": "已核验规则",
    }


def test_undeclared_contract_degrades_when_call_b_itself_fails() -> None:
    config = _undeclared_config()
    assert foundation_required(config) is True
    fallback = empty_deduction_output(config, warning=FALLBACK_WARNING)

    composed = compose_rule_deductions(
        config=config,
        dimension_output=fallback,
        require_foundation=foundation_required(config),
    )

    assert composed.get("aesthetic_score") is None


def test_undeclared_contract_fails_closed_when_call_b_omits_score() -> None:
    config = _undeclared_config()

    with pytest.raises(BAestheticFoundationError):
        compose_rule_deductions(
            config=config,
            dimension_output=_answered_without_score(config),
            require_foundation=foundation_required(config),
        )


def test_declared_contract_stays_strict_even_on_provider_failure() -> None:
    config = deepcopy(build_inspiration_subcategory_dimensions()["class_one"])
    assert isinstance(config.get("b_aesthetic_foundation"), dict)
    fallback = empty_deduction_output(config, warning=FALLBACK_WARNING)

    with pytest.raises(BAestheticFoundationError):
        compose_rule_deductions(
            config=config,
            dimension_output=fallback,
            require_foundation=foundation_required(config),
        )


# --- 运营手选调用 B 接管规则模式正文（决策：手选版本接管） -------------------


class OperatorPrompt:
    """运营在提示词管理器里手选的调用 B 版本。"""

    def __init__(self, *, user_prompt: str, version: str = "insp-b-v6-levels-20260821",
                 system_prompt: str = "你是资深灵感图审美评估专家，先给基础美感分再逐条核验。") -> None:
        self.stage = "B"
        self.version = version
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt


_TAKEOVER_BODY = "请按以下维度规则逐条核验：\n\n{{dimension_rules}}"


class SchemaFaultClient:
    """真的答了，但输出形状不符合冻结合同（bonus-cap 缺 hit_bonus_rules）。"""

    def __init__(self, config: dict) -> None:
        self.config = config

    async def chat_json(self, system: str, user: str, **_kwargs) -> Response:
        return Response(
            {
                "aesthetic_score": 88,
                "aesthetic_evidence": ["整体可见"],
                "aesthetic_confidence": 0.8,
                "dimensions": {
                    dimension["key"]: {"hit_rules": []}
                    for dimension in self.config["common_group"]["schema_definition"]["dimensions"]
                },
                "overall_note": "缺少加分规则数组",
            }
        )


def test_takeover_helper_accepts_any_non_empty_body() -> None:
    """占位符只决定注入位置，不是能否执行的前提条件。

    要求占位符会让引擎拒掉运营现有的绝大多数调用B版本，而它过去的退路（拿合同
    正文冒名执行）正是必须禁止的静默降级。所以只要有正文就如实执行。
    """
    assert operator_prompt_declares_rule_takeover(
        OperatorPrompt(user_prompt=_TAKEOVER_BODY)
    ) is True
    assert operator_prompt_declares_rule_takeover(
        OperatorPrompt(user_prompt="八维评分，直接给出等级")
    ) is True
    # 只写一处也算有正文：现役多个已发布版本把口径全放在 system 里。
    assert operator_prompt_declares_rule_takeover(
        OperatorPrompt(user_prompt="   ")
    ) is True
    assert operator_prompt_declares_rule_takeover(
        OperatorPrompt(user_prompt="八维评分", system_prompt="  ")
    ) is True
    # 两处都空才算没有可执行内容。
    assert operator_prompt_declares_rule_takeover(
        OperatorPrompt(user_prompt="   ", system_prompt="  ")
    ) is False
    assert operator_prompt_declares_rule_takeover(None) is False


def test_operator_selected_prompt_owns_body_and_server_injects_rules() -> None:
    config = build_inspiration_subcategory_dimensions()["class_one"]
    client = Client()
    operator = OperatorPrompt(user_prompt=_TAKEOVER_BODY)

    output = asyncio.run(
        call_multimodal_for_dimension_deductions(
            "image.jpg", config, client=client, mime_type="image/jpeg",
            precheck={"信息提取": {}}, operator_prompt=operator,
        )
    )

    # 手选正文决定人设，服务端强制注入规则清单与输出结构。
    assert client.system == operator.system_prompt
    assert "请按以下维度规则逐条核验" in client.user
    assert "{{dimension_rules}}" not in client.user
    assert "扣" in client.user
    assert "hit_rules" in client.user
    assert "调用A预检字段" in client.user
    assert output["warning"] is None
    assert output["prompt_identity"]["template_version"] == (
        OPERATOR_PROMPT_TEMPLATE_VERSION
    )
    assert output["prompt_identity"]["operator_prompt_version"] == operator.version
    assert output["prompt_identity"]["bypassed_operator_prompt_version"] is None


def test_response_contract_sample_resists_anchor_copying() -> None:
    """示例值锚定回归：42 条里 14 条照抄示例 88 后定的两道防线。"""
    config = _bonus_cap_config()
    client = Client()
    operator = OperatorPrompt(user_prompt=_TAKEOVER_BODY)
    try:
        asyncio.run(
            call_multimodal_for_dimension_deductions(
                "image.jpg", config, client=client, mime_type="image/jpeg",
                operator_prompt=operator,
            )
        )
    except DimensionDeductionBridgeError:
        # Client 的 mock 响应是 deduction_v1 形状，bonus-cap 解析必然拒绝；
        # 本测试只关注请求侧发出的 prompt，响应侧失败不影响断言。
        pass
    # 防线一：禁抄声明必须紧贴示例出现。
    assert "禁止照抄" in client.user
    assert "独立给出" in client.user
    # 防线二：示例里不得出现任何具体分值——88 被抄 14/42，换 41 后又被抄
    # 13/86，只要是数字就会被抄。示例分值必须是描述式占位字符串。
    import re as _re
    assert not _re.search(r'"aesthetic_score":\s*\d', client.user), (
        "输出契约示例出现了具体分值，会被模型照抄成分布尖峰"
    )
    assert '"aesthetic_score": "按当前图片独立给出的0-100整数"' in client.user


def test_operator_prompt_may_place_precheck_and_contract_itself() -> None:
    config = build_inspiration_subcategory_dimensions()["class_one"]
    client = Client()
    operator = OperatorPrompt(
        user_prompt=(
            "预检：{{precheck_json}}\n规则：{{dimension_rules}}\n输出：{{response_contract}}"
        )
    )

    asyncio.run(
        call_multimodal_for_dimension_deductions(
            "image.jpg", config, client=client, mime_type="image/jpeg",
            precheck={"分类": "class_one"}, operator_prompt=operator,
        )
    )

    assert "{{" not in client.user
    assert client.user.index("预检：") < client.user.index("规则：")
    assert "class_one" in client.user


def test_operator_prompt_without_placeholder_still_runs_faithfully() -> None:
    """没写占位符的手选版本照样如实执行，规则由服务端追加而非要求运营预留。

    这是运营兼容性的核心：忘记写占位符不该改变成绩，也不该让引擎拒单，更不该
    偷偷换成合同正文。运营正文原样执行，服务端在其后补齐规则清单。
    """
    config = build_inspiration_subcategory_dimensions()["class_one"]
    client = Client()
    operator = OperatorPrompt(user_prompt="八维评分，直接输出等级")

    output = asyncio.run(
        call_multimodal_for_dimension_deductions(
            "image.jpg", config, client=client, mime_type="image/jpeg",
            operator_prompt=operator,
        )
    )

    # 运营的 system 与 user 正文都真的发给了模型。
    assert client.system == operator.system_prompt
    assert "八维评分，直接输出等级" in client.user
    # 合同正文的人设绝不能冒名顶替。
    assert "灵感素材质量核验专家" not in client.system
    # 服务端仍然强制注入规则清单与输出合同，运营无法丢掉它们。
    assert "必须逐条核验以下维度规则" in client.user
    assert "visual_structure" in client.user
    assert "调用A预检字段" in client.user
    assert "{{" not in client.user
    # 归因必须落在运营选的版本上，且不再标记为绕过。
    identity = output["prompt_identity"]
    assert identity["operator_prompt_version"] == operator.version
    assert identity["bypassed_operator_prompt_version"] is None
    assert identity["template_version"] == OPERATOR_PROMPT_TEMPLATE_VERSION
    assert output["warning"] is None


def test_operator_prompt_with_only_user_body_still_runs() -> None:
    """只写 user 正文的版本照样如实执行：填一处就够，另一处留空不影响。"""
    config = build_inspiration_subcategory_dimensions()["class_one"]
    client = Client()
    operator = OperatorPrompt(user_prompt=_TAKEOVER_BODY, system_prompt="   ")

    output = asyncio.run(
        call_multimodal_for_dimension_deductions(
            "image.jpg", config, client=client, mime_type="image/jpeg",
            operator_prompt=operator,
        )
    )

    assert "visual_structure" in client.user
    assert output["prompt_identity"]["operator_prompt_version"] == operator.version
    assert output["warning"] is None


def test_operator_prompt_with_only_system_body_still_runs() -> None:
    """只写 system 正文的版本照样如实执行：现役多个已发布版本就是这种形状。

    ``model-3d-su-b-v4``、``inspiration-b-v5-anchor-calibration-evidence`` 等
    都把整套评分口径放在 system 里、user 留空，服务端补齐 user 侧内容。
    """
    config = build_inspiration_subcategory_dimensions()["class_one"]
    client = Client()
    operator = OperatorPrompt(user_prompt="", system_prompt="你是灵感图审美评估专家。")

    output = asyncio.run(
        call_multimodal_for_dimension_deductions(
            "image.jpg", config, client=client, mime_type="image/jpeg",
            operator_prompt=operator,
        )
    )

    assert client.system == "你是灵感图审美评估专家。"
    # 服务端补齐全部 user 侧内容，且不留下未替换的占位符。
    assert client.user.startswith("必须逐条核验以下维度规则：")
    assert "visual_structure" in client.user
    assert "调用A预检字段" in client.user
    assert "{{" not in client.user
    assert output["prompt_identity"]["operator_prompt_version"] == operator.version
    assert output["warning"] is None


def test_operator_prompt_worker_placeholders_are_substituted() -> None:
    """worker 调用B路径支持的占位符，规则计分路径必须同样替换。

    现网有 15 个 B 版本写了 {{rubric_version}}、3 个写了 {{image_metadata}}。
    这条路径若不替换，运营会拿到未替换的字面量——等于悄悄跑了个坏提示词。
    """
    config = build_inspiration_subcategory_dimensions()["class_one"]
    client = Client()
    operator = OperatorPrompt(
        user_prompt=(
            "评测规则版本：{{rubric_version}}\n"
            "图片元数据：{{image_metadata}}\n"
            "调用A原始输出：{{previous_output}}"
        )
    )

    output = asyncio.run(
        call_multimodal_for_dimension_deductions(
            "image.jpg", config, client=client, mime_type="image/jpeg",
            operator_prompt=operator,
            image_metadata={"width": 1920, "height": 1080},
            rubric_version="inspiration-rubric-v1",
            previous_output='{"scope":"in_scope"}',
        )
    )

    assert "inspiration-rubric-v1" in client.user
    assert '"width": 1920' in client.user
    assert '{"scope":"in_scope"}' in client.user
    # 一个未替换的占位符都不许发出去。
    assert "{{" not in client.user
    assert output["prompt_identity"]["operator_prompt_version"] == operator.version


def test_operator_prompt_unknown_placeholder_fails_closed() -> None:
    """引擎不认识的占位符必须拒单，而不是把字面量发给模型。"""
    config = build_inspiration_subcategory_dimensions()["class_one"]
    client = Client()
    operator = OperatorPrompt(user_prompt="请参考 {{human_truth}} 打分")

    with pytest.raises(DimensionDeductionBridgeError) as excinfo:
        asyncio.run(
            call_multimodal_for_dimension_deductions(
                "image.jpg", config, client=client, mime_type="image/jpeg",
                operator_prompt=operator,
            )
        )

    assert excinfo.value.code == "operator_prompt_unknown_placeholder"
    detail = str(excinfo.value)
    assert "{{human_truth}}" in detail
    # 必须告诉运营可用的占位符有哪些。
    for supported in SUPPORTED_PLACEHOLDERS:
        assert supported in detail
    assert "修复办法" in detail
    assert client.user == ""


def test_operator_prompt_with_no_body_at_all_fails_closed() -> None:
    """两处都空才拒单，且理由要能指导运营修复。"""
    config = build_inspiration_subcategory_dimensions()["class_one"]
    client = Client()
    operator = OperatorPrompt(user_prompt="  ", system_prompt="   ")

    with pytest.raises(DimensionDeductionBridgeError) as excinfo:
        asyncio.run(
            call_multimodal_for_dimension_deductions(
                "image.jpg", config, client=client, mime_type="image/jpeg",
                operator_prompt=operator,
            )
        )

    assert excinfo.value.code == "operator_prompt_body_empty"
    detail = str(excinfo.value)
    assert operator.version in detail
    assert "修复办法" in detail
    assert "不出分" in detail
    # 拒单时一个字节都不发给 provider。
    assert client.system == ""
    assert client.user == ""


# --- 互斥输出契约守卫：foundation 正文不得上规则命中管线 ---------------------
# 2026-08-25 实锤：v13（八维 grade 契约正文）被挂到 bonus-cap 桥上执行，
# 两套「必须且只能」的 JSON 契约互斥，美感分坍缩为 50/100 两档（灵感图 48%）。


def test_foundation_contract_body_fails_closed_on_rule_hit_path() -> None:
    """v13 式正文（自带八维 grade 输出契约）必须拒单，不能拼给模型瞎猜。"""
    config = build_inspiration_subcategory_dimensions()["class_one"]
    client = Client()
    operator = OperatorPrompt(
        version="insp-b-v13-tier-budget-20260824",
        user_prompt=(
            "八维权重与总分换算（固定执行）……\n"
            "只输出一个严格 JSON 对象，contract_version 固定为 "
            "inspiration-aesthetic-foundation-v1。\n\n{{precheck_json}}"
        ),
    )

    with pytest.raises(DimensionDeductionBridgeError) as excinfo:
        asyncio.run(
            call_multimodal_for_dimension_deductions(
                "image.jpg", config, client=client, mime_type="image/jpeg",
                operator_prompt=operator,
            )
        )

    assert excinfo.value.code == "operator_prompt_contract_conflict"
    detail = str(excinfo.value)
    assert operator.version in detail
    assert "inspiration-aesthetic-foundation-v1" in detail
    assert "不出分" in detail
    # 必须给出两条可行动的修复路径，而不是只说不行。
    assert "美感基座" in detail
    assert "规则命中管线" in detail
    # 拒单时一个字节都不发给 provider。
    assert client.system == ""
    assert client.user == ""


def test_foundation_contract_in_system_prompt_also_fails_closed() -> None:
    """契约声明写在 system 正文里同样互斥，同样拒单。"""
    config = build_inspiration_subcategory_dimensions()["class_one"]
    client = Client()
    operator = OperatorPrompt(
        version="insp-b-v14-good-tier-boundary-20260824",
        system_prompt="按 inspiration-aesthetic-foundation-v2 契约输出八维 grade。",
        user_prompt="{{dimension_rules}}",
    )

    with pytest.raises(DimensionDeductionBridgeError) as excinfo:
        asyncio.run(
            call_multimodal_for_dimension_deductions(
                "image.jpg", config, client=client, mime_type="image/jpeg",
                operator_prompt=operator,
            )
        )

    assert excinfo.value.code == "operator_prompt_contract_conflict"
    assert client.user == ""


def _pure_foundation_config() -> dict:
    """纯基础分：维度保留、规则全空（rev 41 形态）。"""
    config = deepcopy(build_inspiration_subcategory_dimensions()["class_one"])
    for dimension in config["common_group"]["schema_definition"]["dimensions"]:
        dimension["dimension_score_cap"] = 100
        dimension["deduction_rules"] = []
        dimension["bonus_rules"] = []
    return config


class PureFoundationClient:
    """按纯基础分契约作答；with_evaluation=False 模拟漏填逐维评价。"""

    def __init__(self, config: dict, *, with_evaluation: bool = True) -> None:
        self.config = config
        self.with_evaluation = with_evaluation
        self.system = ""
        self.user = ""

    async def chat_json(self, system: str, user: str, **_kwargs) -> Response:
        self.system, self.user = system, user
        dimensions = {}
        for dimension in self.config["common_group"]["schema_definition"]["dimensions"]:
            item: dict = {"hit_rules": [], "hit_bonus_rules": []}
            if self.with_evaluation:
                item["evaluation"] = f"{dimension['label']}表现具体可定位"
            dimensions[dimension["key"]] = item
        return Response(
            {
                "aesthetic_score": 74,
                "aesthetic_evidence": ["整体观察"],
                "aesthetic_confidence": 0.8,
                "dimensions": dimensions,
                "overall_note": "",
            }
        )


def test_pure_foundation_contract_forces_per_dimension_evaluation() -> None:
    """维度锚定必须由输出契约保证：正文口径漂移时模型会在维度间摇摆。"""
    config = _pure_foundation_config()
    client = PureFoundationClient(config)
    output = asyncio.run(
        call_multimodal_for_dimension_deductions(
            "image.jpg", config, client=client, mime_type="image/jpeg"
        )
    )
    # 契约文本：零规则维度带 evaluation 字段、命中只演示空数组
    assert '"evaluation"' in client.user
    assert '"rule_id": "命中的规则ID"' not in client.user
    # 逐维评价随结构落库
    for item in output["dimensions"].values():
        assert item["evaluation"].endswith("表现具体可定位")


def test_pure_foundation_missing_evaluation_fails_closed_retryable() -> None:
    config = _pure_foundation_config()
    with pytest.raises(DimensionDeductionBridgeError) as excinfo:
        asyncio.run(
            call_multimodal_for_dimension_deductions(
                "image.jpg", config,
                client=PureFoundationClient(config, with_evaluation=False),
                mime_type="image/jpeg",
            )
        )
    assert excinfo.value.code == "dimension_evaluation_missing"
    # 措辞抖动类缺失可重试
    assert excinfo.value.technical_error_type == "transient_parse"
    assert excinfo.value.retryable is True


def test_foreign_bridge_placeholder_detects_rule_hit_bodies() -> None:
    """镜像守卫：规则命中正文不得上美感基座管线（run 91 全拒的根因）。"""
    from app.dimension_deduction_bridge import foreign_bridge_placeholder

    assert foreign_bridge_placeholder("", "先核验：\n{{dimension_rules}}") == (
        "{{dimension_rules}}"
    )
    assert foreign_bridge_placeholder(
        "输出按 {{response_contract}}", ""
    ) == "{{response_contract}}"
    # foundation 原生正文（无 bridge 占位符）不受影响，{{precheck_json}} 是共享占位符。
    assert foreign_bridge_placeholder(
        "八维评分口径……", "{{precheck_json}}"
    ) is None
    assert foreign_bridge_placeholder(None, None) is None


def test_rule_hit_native_body_passes_conflict_guard() -> None:
    """为规则命中管线写的正文（不声明整套输出契约）不受守卫影响。"""
    assert operator_prompt_conflicting_contract(
        OperatorPrompt(user_prompt=_TAKEOVER_BODY)
    ) is None
    assert operator_prompt_conflicting_contract(None) is None
    # 守卫清单必须覆盖 foundation 契约的全部现行版本串。
    assert "inspiration-aesthetic-foundation-v1" in CONFLICTING_OUTPUT_CONTRACT_MARKERS
    assert "inspiration-aesthetic-foundation-v2" in CONFLICTING_OUTPUT_CONTRACT_MARKERS


# --- 兜底拆分：provider 故障 fail-open，输出形状不符 fail-closed -------------


def test_contract_shape_fault_fails_closed_instead_of_full_marks() -> None:
    config = _bonus_cap_config()

    with pytest.raises(DimensionDeductionBridgeError) as excinfo:
        asyncio.run(
            call_multimodal_for_dimension_deductions(
                "image.jpg", config, client=SchemaFaultClient(config),
                mime_type="image/jpeg",
            )
        )
    assert excinfo.value.code == "dimension_output_invalid"


def test_provider_outage_records_reason_on_fail_open() -> None:
    """fail-open 仅存在于显式 opt-out 基础分的合同；原因仍要留痕。"""
    config = dict(build_inspiration_subcategory_dimensions()["class_one"])
    config["b_aesthetic_foundation"] = {"enabled": False}

    output = asyncio.run(
        call_multimodal_for_dimension_deductions(
            "image.jpg", config, client=Client(fail=True), mime_type="image/jpeg",
        )
    )

    assert output["warning"] == FALLBACK_WARNING
    assert "TimeoutError" in output["provider_error"]
