"""Declarative base contract for spatial containers; membership grants no capabilities."""
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NodeContainerDefinition:
    member_traits: frozenset[str] = frozenset()
    parentable: bool = True
    connectable: bool = True
    min_size: tuple[int, int] = (800, 500)
    # Left, top, right, bottom space reserved for the container's controls.
    content_inset: tuple[int, int, int, int] = (24, 110, 24, 24)
    max_members: int = 100
    document_field: str | None = None
    member_type: str | None = None

    def catalog_item(self):
        return {"member_traits": sorted(self.member_traits), "parentable": self.parentable,
                "connectable": self.connectable, "min_size": self.min_size,
                "content_inset": self.content_inset, "max_members": self.max_members,
                "document_field": self.document_field}
