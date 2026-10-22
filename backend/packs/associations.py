"""Bounded, permission-free associations shared by passive authoring paths."""
from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from backend.packs.identifiers import IDENTIFIER


Identifier = Annotated[str, Field(pattern=IDENTIFIER, max_length=128, strict=True)]


def _unique(values):
    if len(values) != len(set(values)):
        raise ValueError('Association selectors and card traits must be unique')
    return values


CardTraits = Annotated[tuple[Identifier, ...], Field(max_length=100), AfterValidator(_unique)]


class PassiveAssociation(BaseModel):
    """Describe graph meaning without granting operations or data access.

    Type selectors match any listed type; trait selectors require every listed
    trait. When both are supplied, both constraints must match.
    """
    model_config = ConfigDict(extra='forbid', frozen=True)

    id: Identifier
    label: str = Field(min_length=1, max_length=120, strict=True)
    short_label: str = Field(default='Link', min_length=1, max_length=40, strict=True)
    description: str = Field(default='', max_length=1000, strict=True)
    source_types: CardTraits = ()
    source_traits: CardTraits = ()
    target_types: CardTraits = ()
    target_traits: CardTraits = ()
    directions: Annotated[tuple[Literal['forward', 'bidirectional'], ...],
                          Field(min_length=1, max_length=2), AfterValidator(_unique)] = ('forward',)

    @model_validator(mode='after')
    def bounded_endpoints(self) -> Self:
        if not (self.source_types or self.source_traits) or not (self.target_types or self.target_traits):
            raise ValueError('Passive associations require explicit selectors for both endpoints')
        return self


def validate_card_traits(owner: str, traits) -> frozenset[str]:
    if any(not trait.startswith(owner + '.') for trait in traits):
        raise ValueError('Passive card traits must use their owner namespace')
    return frozenset(traits)


def validate_associations(owner: str, cards, associations) -> None:
    """Check ownership using actual contributed cards, never ID inference."""
    own_types = {card.id for card in cards}
    own_traits = {trait for card in cards for trait in card.traits}
    seen = set()
    for association in associations:
        if not association.id.startswith(owner + '.'):
            raise ValueError('Passive association IDs must use their owner namespace')
        if association.id in seen:
            raise ValueError('Passive association IDs must be unique')
        seen.add(association.id)
        if set(association.source_types) - own_types:
            raise ValueError('Passive association source types must belong to the declaring Pack')
        if association.source_traits and (
                any(not trait.startswith(owner + '.') for trait in association.source_traits)
                or set(association.source_traits) - own_traits):
            raise ValueError('Passive association source traits must be declared by its own cards')


def register_associations(registration, associations) -> None:
    from backend.plugins.registry import RelationshipDefinition

    validate_associations(registration.descriptor.id, registration.nodes.values(), associations)
    for association in associations:
        registration.register_relationship(RelationshipDefinition(
            id=association.id, label=association.label, short_label=association.short_label,
            description=association.description, source_types=frozenset(association.source_types),
            target_types=frozenset(association.target_types), source_traits=frozenset(association.source_traits),
            target_traits=frozenset(association.target_traits), directions=frozenset(association.directions),
            capabilities=(), data_read=False, templateable=True))
