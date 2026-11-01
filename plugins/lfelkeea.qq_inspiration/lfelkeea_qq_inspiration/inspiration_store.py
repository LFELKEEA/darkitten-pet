"""灵感库的增删查，数据放在 api.storage 里。"""

from __future__ import annotations

import re
import time
import uuid


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


class InspirationStore:
    def __init__(self, api) -> None:
        self.api = api
        raw = api.storage.get("inspirations")
        self.items: list[dict] = raw if isinstance(raw, list) else []

    def save(self) -> None:
        self.api.storage["inspirations"] = self.items
        self.api.save_storage()

    def all(self) -> list[dict]:
        return list(self.items)

    def add(
        self,
        title: str,
        idea: str,
        source: str = "",
        tags: list[str] | None = None,
    ) -> dict | None:
        body = str(idea or "").strip()
        if not body:
            return None
        item = {
            "id": uuid.uuid4().hex[:12],
            "title": (str(title or "").strip() or body[:20]).strip(),
            "idea": body,
            "source": str(source or "").strip(),
            "tags": [str(tag).strip()[:12] for tag in (tags or []) if str(tag).strip()][:5],
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.items.insert(0, item)
        self.save()
        return item

    def remove(self, item_id: str) -> bool:
        return self.remove_many([item_id]) > 0

    def remove_many(self, item_ids) -> int:
        targets = {str(item_id) for item_id in (item_ids or []) if item_id}
        if not targets:
            return 0
        before = len(self.items)
        self.items = [item for item in self.items if item.get("id") not in targets]
        removed = before - len(self.items)
        if removed:
            self.save()
        return removed

    def has_idea(self, idea: str) -> bool:
        key = _normalize(idea)
        if not key:
            return False
        return any(_normalize(item.get("idea")) == key for item in self.items)

    def source_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.items:
            source = str(item.get("source") or "未标注")
            counts[source] = counts.get(source, 0) + 1
        return counts
