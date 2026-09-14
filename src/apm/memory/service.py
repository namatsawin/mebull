"""Persistent Memory (spec §43-44).

Knowledge is stored per (portfolio, category, key). Updates never silently overwrite —
each write appends a MemoryRevision and bumps the item's revision (spec §44). The global
``memory_version`` (e.g. "M42") is the count of revisions and is stamped onto every
decision so history can be reconstructed.
"""

from __future__ import annotations

from sqlalchemy import func, select

from apm.config import get_settings
from apm.db import session_scope
from apm.db.models import Memory, MemoryRevision
from apm.observability import get_logger

log = get_logger("memory")


class MemoryService:
    def __init__(self, portfolio_id: str | None = None) -> None:
        self._portfolio_id = portfolio_id or get_settings().portfolio_id

    async def current_version(self) -> str:
        async with session_scope() as s:
            count = await s.scalar(
                select(func.count()).select_from(MemoryRevision).join(Memory).where(
                    Memory.portfolio_id == self._portfolio_id
                )
            )
        return f"M{count or 0}"

    async def remember(
        self,
        category: str,
        key: str,
        content: str,
        *,
        tags: list[str] | None = None,
        reason: str | None = None,
    ) -> str:
        """Create or revise a memory item. Returns the new memory_version."""
        async with session_scope() as s:
            item = await s.scalar(
                select(Memory).where(
                    Memory.portfolio_id == self._portfolio_id,
                    Memory.category == category,
                    Memory.key == key,
                )
            )
            if item is None:
                item = Memory(
                    portfolio_id=self._portfolio_id,
                    category=category,
                    key=key,
                    content=content,
                    current_revision=1,
                    tags=tags or [],
                )
                s.add(item)
                await s.flush()
                s.add(
                    MemoryRevision(
                        memory_id=item.id, revision=1, content=content, reason=reason
                    )
                )
            else:
                item.current_revision += 1
                item.content = content
                if tags is not None:
                    item.tags = tags
                s.add(
                    MemoryRevision(
                        memory_id=item.id,
                        revision=item.current_revision,
                        content=content,
                        reason=reason,
                    )
                )
        version = await self.current_version()
        log.info("memory.remember", category=category, key=key, version=version)
        return version

    async def recall(
        self,
        *,
        category: str | None = None,
        query: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Relevance retrieval (spec §46). Simple keyword filter for now; the interface
        is stable so a semantic index can be swapped in later without touching callers."""
        async with session_scope() as s:
            stmt = select(Memory).where(Memory.portfolio_id == self._portfolio_id)
            if category:
                stmt = stmt.where(Memory.category == category)
            if query:
                stmt = stmt.where(Memory.content.ilike(f"%{query}%"))
            stmt = stmt.order_by(Memory.updated_at.desc()).limit(limit)
            rows = (await s.scalars(stmt)).all()
        return [
            {
                "category": r.category,
                "key": r.key,
                "content": r.content,
                "revision": r.current_revision,
                "tags": r.tags,
            }
            for r in rows
        ]
