import pytest

from app.source_identity_probe import SourceIdentityProbeError, build_three_d_su_identity_probe


def test_probe_contains_count_null_duplicate_and_res_id_queries() -> None:
    bundle = build_three_d_su_identity_probe("aliyun_3d66_dw.dim_res_info_union")
    assert set(bundle.queries) == {"scope", "nulls", "duplicates", "res_id_conflicts"}
    assert "res_type IN (1, 6)" in bundle.queries["scope"]
    assert "GROUP BY res_type, ll_id" in bundle.queries["duplicates"]
    assert "COUNT(DISTINCT res_id)" in bundle.queries["res_id_conflicts"]
    assert len(bundle.probe_hash) == 64


def test_probe_hash_is_stable() -> None:
    first = build_three_d_su_identity_probe("aliyun_3d66_dw.dim_res_info_union")
    second = build_three_d_su_identity_probe("aliyun_3d66_dw.dim_res_info_union")
    assert first.probe_hash == second.probe_hash


@pytest.mark.parametrize("table", ["x;DROP TABLE y", "x y", "`secret`", "x/../y"])
def test_probe_rejects_unsafe_identifiers(table: str) -> None:
    with pytest.raises(SourceIdentityProbeError, match="表名"):
        build_three_d_su_identity_probe(table)
