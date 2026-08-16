from __future__ import annotations

import argparse
import json

from backend.app.source_identity_probe import build_three_d_su_identity_probe


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--table",
        default="aliyun_3d66_dw.dim_res_info_union",
    )
    args = parser.parse_args()
    bundle = build_three_d_su_identity_probe(args.table)
    print(json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
