"""Read-only diagnostics for the frozen inspiration truth population."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Asset


PATTERN = re.compile(r"(?:^|[/\\_])(好|中等|中差|极差|过滤)_")


with SessionLocal() as db:
    assets = db.scalars(select(Asset).order_by(Asset.id)).all()
    by_status: dict[str, Counter[str]] = defaultdict(Counter)
    by_prefix: dict[str, Counter[str]] = defaultdict(Counter)
    matched = []
    for asset in assets:
        match = PATTERN.search(asset.original_name or "")
        if match is None:
            continue
        rating = match.group(1)
        by_status[asset.status][rating] += 1
        prefix = (asset.original_name or "").replace("\\", "/").split("/", 1)[0]
        by_prefix[prefix][rating] += 1
        matched.append(asset)
    print(
        json.dumps(
            {
                "by_status": {
                    status: dict(counter) for status, counter in sorted(by_status.items())
                },
                "by_prefix": {
                    prefix: dict(counter) for prefix, counter in sorted(by_prefix.items())
                },
                "id_ranges": {
                    status: [
                        min(item.id for item in matched if item.status == status),
                        max(item.id for item in matched if item.status == status),
                    ]
                    for status in sorted(by_status)
                },
                "unmatched": [
                    {
                        "id": item.id,
                        "name": item.original_name,
                        "status": item.status,
                        "mime_type": item.mime_type,
                    }
                    for item in assets
                    if PATTERN.search(item.original_name or "") is None
                ],
                "tail": [
                    {
                        "id": item.id,
                        "name": item.original_name,
                        "status": item.status,
                        "created_at": item.created_at.isoformat(),
                    }
                    for item in assets[-25:]
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
