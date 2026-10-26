"""Single release-owned Marketplace endpoint; never a user preference.

The deployed service origin contains no API path. Settings resolves the
developer override without network I/O; Store requests connect lazily.
"""

OFFICIAL_MARKETPLACE_URL: str | None = "https://open-agent-world.space"
