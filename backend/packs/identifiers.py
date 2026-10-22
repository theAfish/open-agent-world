"""Shared identity grammars; distribution names also become filesystem paths.

Runtime identifiers retain their existing spelling for persisted cards and edges.
Distribution IDs use the portable, dotted archive grammar on every authoring path.
These are separate concepts: a runtime owner need not equal its distribution.
"""

IDENTIFIER = r"^[a-z][a-z0-9]*(?:[._:/-][a-z0-9]+)*$"
PACK_ID = r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+$"
