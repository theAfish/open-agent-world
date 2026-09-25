"""Single release-owned Marketplace endpoint; never a user preference.

Set this only after the operator has deployed and verified the permanent HTTPS
service. An unset value truthfully keeps unreleased builds Store-unavailable.
The developer override is resolved by Settings, without doing network I/O.
"""

OFFICIAL_MARKETPLACE_URL: str | None = None
