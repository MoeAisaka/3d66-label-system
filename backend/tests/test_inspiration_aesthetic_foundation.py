from __future__ import annotations

import copy
import hashlib
from types import SimpleNamespace

import pytest

from app.inspiration_aesthetic_foundation import (
    AESTHETIC_CALL_B_VERSION,
    ANCHORS,
    DIMENSION_KEYS,
    AestheticFoundationError,
    anchor_request_from_contract,
    anchor_samples,
    apply_aesthetic_v3_rules,
    build_prompt,
    canonical_foundation,
    validate_aesthetic_output,
)
from app.inspiration_category_seed import (
    build_inspiration_classification_map,
    build_inspiration_v3_contract,
)
from app.category_evaluation_contract import (
    CategoryEvaluationContractError,
    validate_category_evaluation_contract,
)


def valid_payload(score: int = 89) -> dict:
    return {
        "contract_version": "inspiration-aesthetic-foundation-v1",
        "aesthetic_score": score,
        "dimensions": {
            key: {
                "grade": 3,
                "evidence": [f"{key} 可见证据"],
                "shortcomings": [f"{key} 可见不足"],
            }
            for key in DIMENSION_KEYS
        },
        "overall_evidence": ["整体可见证据"],
        "confidence": 0.82,
    }


def precheck(*, redline: str | None = None, track: str = "建筑设计") -> dict:
    reason = redline or "无红线"
    return {
        "classification": {
            "scope_status": "in_scope",
            "primary_category": track,
            "primary_confidence": 0.95,
        },
        "production_fields": {"reason": [reason], "trait": "实景照片"},
        "hard_defects": [],
        "image_defects": [],
        "decisive_signal_validation": {"status": "valid"},
    }


def _write_anchor(tmp_path, *, asset_id: int, level: str, suffix: str = ".jpeg") -> dict:
    payload = f"anchor-{asset_id}-{level}".encode("utf-8")
    stored_name = f"asset-{asset_id}{suffix}"
    (tmp_path / stored_name).write_bytes(payload)
    return {
        "asset_id": asset_id,
        "level": level,
        "stored_name": stored_name,
        "mime_type": "image/jpeg" if suffix == ".jpeg" else "image/png",
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _asset_rows(*anchors: dict) -> dict[int, SimpleNamespace]:
    return {
        anchor["asset_id"]: SimpleNamespace(
            id=anchor["asset_id"],
            stored_name=anchor["stored_name"],
            mime_type=anchor["mime_type"],
            sha256=anchor["sha256"],
        )
        for anchor in anchors
    }


def test_candidate_anchor_resolves_internal_filename_from_asset_metadata(tmp_path) -> None:
    anchors = [
        _write_anchor(tmp_path, asset_id=500 + index, level=f"L{index}")
        for index in range(1, 5)
    ]
    candidate_payload = b"candidate-l5-anchor"
    candidate_stored_name = "server-owned-candidate-l5.jpeg"
    (tmp_path / candidate_stored_name).write_bytes(candidate_payload)
    anchors.append({
        "asset_id": 339,
        "level": "L5",
        "mime_type": "image/jpeg",
        "sha256": hashlib.sha256(candidate_payload).hexdigest(),
    })
    assets_by_id = _asset_rows(*anchors[:4])
    for anchor in anchors[:4]:
        anchor.pop("stored_name")
    assets_by_id[339] = SimpleNamespace(
        id=339,
        stored_name=candidate_stored_name,
        mime_type="image/jpeg",
        sha256=hashlib.sha256(candidate_payload).hexdigest(),
    )
    target = tmp_path / "target.jpeg"
    target.write_bytes(b"target")

    samples, max_image_count = anchor_request_from_contract(
        {"aesthetic_foundation": {"anchors": anchors}},
        tmp_path,
        target,
        "image/jpeg",
        assets_by_id=assets_by_id,
    )

    assert samples[-2] == ("Owner锚图 L5（asset 339）", tmp_path / candidate_stored_name, "image/jpeg")
    assert max_image_count == 6


@pytest.mark.parametrize(
    ("asset", "expected_code"),
    [
        (None, "anchor_asset_missing"),
        (
            SimpleNamespace(
                id=339,
                stored_name="candidate.jpeg",
                mime_type="image/png",
                sha256="a" * 64,
            ),
            "anchor_asset_mime_mismatch",
        ),
        (
            SimpleNamespace(
                id=339,
                stored_name="candidate.jpeg",
                mime_type="image/jpeg",
                sha256="a" * 64,
            ),
            "anchor_asset_hash_mismatch",
        ),
    ],
)
def test_candidate_anchor_fails_closed_when_asset_metadata_drifted(
    tmp_path, asset, expected_code: str
) -> None:
    payload = b"candidate-l5-anchor"
    candidate_anchors = [
        _write_anchor(tmp_path, asset_id=100 + index, level=f"L{index}")
        for index in range(1, 5)
    ]
    candidate = {
        "asset_id": 339,
        "level": "L5",
        "mime_type": "image/jpeg",
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    target = tmp_path / "target.jpeg"
    target.write_bytes(b"target")
    assets_by_id = _asset_rows(*candidate_anchors)
    for anchor in candidate_anchors:
        anchor.pop("stored_name")
    if asset is not None:
        assets_by_id[339] = asset

    with pytest.raises(AestheticFoundationError) as exc_info:
        anchor_request_from_contract(
            {"aesthetic_foundation": {"anchors": [*candidate_anchors, candidate]}},
            tmp_path,
            target,
            "image/jpeg",
            assets_by_id=assets_by_id,
        )

    assert exc_info.value.code == expected_code


def test_candidate_anchor_fails_closed_when_internal_file_hash_drifted(tmp_path) -> None:
    candidate_payload = b"expected-candidate-bytes"
    (tmp_path / "candidate.jpeg").write_bytes(b"wrong-candidate-bytes")
    candidate_anchors = [
        _write_anchor(tmp_path, asset_id=100 + index, level=f"L{index}")
        for index in range(1, 5)
    ]
    assets_by_id = _asset_rows(*candidate_anchors)
    for anchor in candidate_anchors:
        anchor.pop("stored_name")
    anchors = [*candidate_anchors, {
        "asset_id": 339,
        "level": "L5",
        "mime_type": "image/jpeg",
        "sha256": hashlib.sha256(candidate_payload).hexdigest(),
    }]
    target = tmp_path / "target.jpeg"
    target.write_bytes(b"target")

    with pytest.raises(AestheticFoundationError) as exc_info:
        anchor_request_from_contract(
            {"aesthetic_foundation": {"anchors": anchors}},
            tmp_path,
            target,
            "image/jpeg",
            assets_by_id={
                **assets_by_id,
                339: SimpleNamespace(
                    id=339,
                    stored_name="candidate.jpeg",
                    mime_type="image/jpeg",
                    sha256=hashlib.sha256(candidate_payload).hexdigest(),
                )
            },
        )

    assert exc_info.value.code == "anchor_file_hash_mismatch"


def test_anchor_samples_keep_existing_four_anchor_order(tmp_path) -> None:
    anchors = [
        _write_anchor(tmp_path, asset_id=100 + index, level=f"L{index}")
        for index in range(1, 5)
    ]
    target = tmp_path / "target.jpeg"
    target.write_bytes(b"target")

    samples = anchor_samples(
        tmp_path,
        target,
        "image/jpeg",
        anchors=anchors,
    )

    assert [label for label, *_ in samples] == [
        "Owner锚图 L1（asset 101）",
        "Owner锚图 L2（asset 102）",
        "Owner锚图 L3（asset 103）",
        "Owner锚图 L4（asset 104）",
        "待评图片（禁止把锚图等级直接当作输出）",
    ]


def test_anchor_request_uses_frozen_five_anchor_contract(tmp_path) -> None:
    anchors = [
        _write_anchor(tmp_path, asset_id=200 + index, level=f"L{index}")
        for index in range(1, 6)
    ]
    assets_by_id = _asset_rows(*anchors)
    for anchor in anchors:
        anchor.pop("stored_name")
    contract = {"aesthetic_foundation": {"anchors": anchors}}
    target = tmp_path / "target.jpeg"
    target.write_bytes(b"target")

    samples, max_image_count = anchor_request_from_contract(
        contract,
        tmp_path,
        target,
        "image/jpeg",
        assets_by_id=assets_by_id,
    )

    assert [label for label, *_ in samples][-2:] == [
        "Owner锚图 L5（asset 205）",
        "待评图片（禁止把锚图等级直接当作输出）",
    ]
    assert max_image_count == 6


def test_anchor_request_does_not_fall_back_when_frozen_anchors_missing(tmp_path) -> None:
    target = tmp_path / "target.jpeg"
    target.write_bytes(b"target")

    with pytest.raises(AestheticFoundationError) as exc_info:
        anchor_request_from_contract(
            {"aesthetic_foundation": {}},
            tmp_path,
            target,
            "image/jpeg",
        )

    assert exc_info.value.code == "anchor_contract_missing"


def test_anchor_samples_fail_closed_on_missing_file(tmp_path) -> None:
    anchors = [
        _write_anchor(tmp_path, asset_id=300 + index, level=f"L{index}")
        for index in range(1, 5)
    ]
    (tmp_path / anchors[0]["stored_name"]).unlink()
    target = tmp_path / "target.jpeg"
    target.write_bytes(b"target")

    with pytest.raises(AestheticFoundationError) as exc_info:
        anchor_samples(
            tmp_path,
            target,
            "image/jpeg",
            anchors=anchors,
        )

    assert exc_info.value.code == "anchor_missing"


@pytest.mark.parametrize("unsafe_name", ["../outside.jpeg", "..\\outside.jpeg", "nested/file.jpeg", "nested\\file.jpeg"])
def test_anchor_samples_reject_cross_platform_unsafe_legacy_stored_name(
    tmp_path, unsafe_name: str
) -> None:
    anchors = [
        _write_anchor(tmp_path, asset_id=320 + index, level=f"L{index}")
        for index in range(1, 5)
    ]
    anchors[0]["stored_name"] = unsafe_name
    target = tmp_path / "target.jpeg"
    target.write_bytes(b"target")

    with pytest.raises(AestheticFoundationError) as exc_info:
        anchor_samples(tmp_path, target, "image/jpeg", anchors=anchors)

    assert exc_info.value.code == "anchor_contract_invalid"


def test_candidate_anchor_rejects_cross_platform_unsafe_asset_stored_name(tmp_path) -> None:
    anchors = [
        _write_anchor(tmp_path, asset_id=330 + index, level=f"L{index}")
        for index in range(1, 5)
    ]
    assets_by_id = _asset_rows(*anchors)
    for anchor in anchors:
        anchor.pop("stored_name")
    anchors.append({
        "asset_id": 339,
        "level": "L5",
        "mime_type": "image/jpeg",
        "sha256": "f" * 64,
    })
    assets_by_id[339] = SimpleNamespace(
        id=339,
        stored_name="nested\\candidate.jpeg",
        mime_type="image/jpeg",
        sha256="f" * 64,
    )
    target = tmp_path / "target.jpeg"
    target.write_bytes(b"target")

    with pytest.raises(AestheticFoundationError) as exc_info:
        anchor_request_from_contract(
            {"aesthetic_foundation": {"anchors": anchors}},
            tmp_path,
            target,
            "image/jpeg",
            assets_by_id=assets_by_id,
        )

    assert exc_info.value.code == "anchor_asset_stored_name_invalid"


def test_anchor_samples_fail_closed_on_hash_mismatch(tmp_path) -> None:
    anchors = [
        _write_anchor(tmp_path, asset_id=350 + index, level=f"L{index}")
        for index in range(1, 5)
    ]
    anchors[0]["sha256"] = "0" * 64
    target = tmp_path / "target.jpeg"
    target.write_bytes(b"target")

    with pytest.raises(AestheticFoundationError) as exc_info:
        anchor_samples(
            tmp_path,
            target,
            "image/jpeg",
            anchors=anchors,
        )

    assert exc_info.value.code == "anchor_hash_mismatch"


def test_anchor_contract_rejects_duplicate_or_out_of_order_levels(tmp_path) -> None:
    anchors = [
        _write_anchor(tmp_path, asset_id=401, level="L1"),
        _write_anchor(tmp_path, asset_id=402, level="L1"),
    ]
    target = tmp_path / "target.jpeg"
    target.write_bytes(b"target")

    with pytest.raises(AestheticFoundationError) as exc_info:
        anchor_samples(
            tmp_path,
            target,
            "image/jpeg",
            anchors=anchors,
        )

    assert exc_info.value.code == "anchor_contract_invalid"


def test_v3_contract_rejects_malformed_frozen_fifth_anchor() -> None:
    contract = build_inspiration_v3_contract()
    contract["aesthetic_foundation"]["anchors"].append({
        "asset_id": 339,
        "level": "L4",
        "stored_name": "candidate-l5.jpeg",
        "mime_type": "image/jpeg",
        "sha256": "f" * 64,
    })

    with pytest.raises(CategoryEvaluationContractError) as exc_info:
        validate_category_evaluation_contract(contract)

    assert exc_info.value.code == "aesthetic_foundation.anchor_contract_invalid"


def test_v3_contract_accepts_all_public_candidate_anchor_metadata() -> None:
    contract = build_inspiration_v3_contract()
    for anchor in contract["aesthetic_foundation"]["anchors"]:
        anchor.pop("stored_name")
    contract["aesthetic_foundation"]["anchors"].append({
        "asset_id": 339,
        "level": "L5",
        "mime_type": "image/jpeg",
        "sha256": "f" * 64,
    })

    validate_category_evaluation_contract(contract)


def test_v3_contract_rejects_mixed_legacy_and_public_anchor_metadata() -> None:
    contract = build_inspiration_v3_contract()
    contract["aesthetic_foundation"]["anchors"][3].pop("stored_name")

    with pytest.raises(CategoryEvaluationContractError) as exc_info:
        validate_category_evaluation_contract(contract)

    assert exc_info.value.code == "aesthetic_foundation.anchor_contract_invalid"


def test_v3_contract_rejects_legacy_five_anchor_contract() -> None:
    contract = build_inspiration_v3_contract()
    contract["aesthetic_foundation"]["anchors"].append({
        "asset_id": 339,
        "level": "L5",
        "stored_name": "must-not-enter-candidate-contract.jpeg",
        "mime_type": "image/jpeg",
        "sha256": "f" * 64,
    })

    with pytest.raises(CategoryEvaluationContractError) as exc_info:
        validate_category_evaluation_contract(contract)

    assert exc_info.value.code == "aesthetic_foundation.anchor_contract_invalid"


def test_contract_is_new_b_and_uses_temporary_downward_mapping() -> None:
    contract = build_inspiration_v3_contract()
    assert contract["prompt_bindings"]["call_b_version"] == AESTHETIC_CALL_B_VERSION
    block = contract["aesthetic_foundation"]
    assert block["calibration_status"] == "temporary_pending_calibration"
    assert block["boundary_policy"] == "floor_to_lower_band"
    assert block["score_thresholds"] == [
        {"min_score": 90, "level": "L1"},
        {"min_score": 75, "level": "L2"},
        {"min_score": 60, "level": "L3"},
        {"min_score": 0, "level": "L4"},
    ]


def test_strict_validator_accepts_exact_eight_dimensions() -> None:
    normalized = validate_aesthetic_output(valid_payload())
    assert normalized["aesthetic_score"] == 89
    assert tuple(normalized["dimensions"]) == DIMENSION_KEYS


def test_call_b_prompt_contains_a_complete_auditable_json_instance() -> None:
    prompt = build_prompt()
    assert AESTHETIC_CALL_B_VERSION == (
        "inspiration-b-v5-anchor-calibration-evidence-20260807"
    )
    assert '"contract_version": "inspiration-aesthetic-foundation-v1"' in prompt
    assert '"evidence": ["必须填写至少一条待评图可见证据"]' in prompt
    assert '"shortcomings": []' in prompt
    assert '"overall_evidence": ["必须填写至少一条整体可见证据"]' in prompt
    for key in DIMENSION_KEYS:
        assert f'"{key}"' in prompt


@pytest.mark.parametrize("bad_grade", [0, 6, None, True])
def test_strict_validator_rejects_zero_and_illegal_grades(bad_grade) -> None:
    payload = valid_payload()
    payload["dimensions"][DIMENSION_KEYS[0]]["grade"] = bad_grade
    with pytest.raises(AestheticFoundationError, match="grade"):
        validate_aesthetic_output(payload)


def test_strict_validator_rejects_missing_extra_and_final_fields() -> None:
    missing = valid_payload()
    missing["dimensions"].pop(DIMENSION_KEYS[-1])
    with pytest.raises(AestheticFoundationError, match="八维"):
        validate_aesthetic_output(missing)
    extra = valid_payload()
    extra["dimensions"]["extra"] = copy.deepcopy(
        extra["dimensions"][DIMENSION_KEYS[0]]
    )
    with pytest.raises(AestheticFoundationError, match="八维"):
        validate_aesthetic_output(extra)
    forbidden = valid_payload()
    forbidden["final_level"] = "L2"
    with pytest.raises(AestheticFoundationError, match="最终等级"):
        validate_aesthetic_output(forbidden)


@pytest.mark.parametrize(
    ("score", "expected"),
    [(100, "L1"), (90, "L1"), (89, "L2"), (75, "L2"),
     (74, "L3"), (60, "L3"), (59, "L4"), (0, "L4")],
)
def test_boundary_mapping_is_deterministic_and_downward(score: int, expected: str) -> None:
    contract = build_inspiration_v3_contract()
    result = apply_aesthetic_v3_rules(
        contract=contract,
        classification_map=build_inspiration_classification_map(),
        precheck=precheck(),
        foundation=valid_payload(score),
    )
    assert result["raw_level"] == expected


def test_track_cap_and_hard_defect_do_not_pollute_foundation() -> None:
    contract = build_inspiration_v3_contract()
    payload = valid_payload(96)
    before = canonical_foundation(payload)
    item = precheck(track="意向图")
    item["hard_defects"] = ["garish_color"]
    result = apply_aesthetic_v3_rules(
        contract=contract,
        classification_map=build_inspiration_classification_map(),
        precheck=item,
        foundation=payload,
    )
    assert result["inspiration_aesthetic_score"] == 96
    assert result["score"] == 60
    assert result["level"] == "L3"
    assert canonical_foundation(payload) == before
    assert result["foundation_before_rules"] == result["foundation_after_rules"]


def test_redline_is_l5_and_does_not_require_call_b() -> None:
    result = apply_aesthetic_v3_rules(
        contract=build_inspiration_v3_contract(),
        classification_map=build_inspiration_classification_map(),
        precheck=precheck(redline="是截图"),
        foundation=None,
    )
    assert result["hard_reject"] is True
    assert result["level"] == "L5"
    assert result["inspiration_aesthetic_score"] is None


def test_casual_signal_without_disorder_does_not_overreject_foundation() -> None:
    item = precheck(redline="是随手拍")
    result = apply_aesthetic_v3_rules(
        contract=build_inspiration_v3_contract(),
        classification_map=build_inspiration_classification_map(),
        precheck=item,
        foundation=valid_payload(61),
    )
    assert result["hard_reject"] is False
    assert result["inspiration_aesthetic_score"] == 61
    assert result["foundation_before_rules"] == result["foundation_after_rules"]


def test_casual_signal_with_disorder_remains_l5_redline() -> None:
    item = precheck(redline="是随手拍")
    item["hard_defects"] = ["careless_composition"]
    result = apply_aesthetic_v3_rules(
        contract=build_inspiration_v3_contract(),
        classification_map=build_inspiration_classification_map(),
        precheck=item,
        foundation=None,
    )
    assert result["hard_reject"] is True
    assert result["level"] == "L5"
    assert result["hit_rules"] == ["casual_snapshot"]
    assert result["inspiration_aesthetic_score"] is None
