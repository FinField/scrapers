"""Source adapter contract: every source turns an Entity into a FactSet."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from finfacts.model import Entity, FactSet


class FactSource(ABC):
    """One upstream data source (SEC EDGAR, GLEIF, ...)."""

    kind: str = "base"

    @abstractmethod
    def covers(self, entity: Entity) -> bool:
        """Whether this source can supply facts for the entity."""

    @abstractmethod
    def fetch(self, entity: Entity) -> Optional[FactSet]:
        """Fetch and normalize all facts for the entity, or None if absent."""
