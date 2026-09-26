"""Bounded, read-only model discovery. Never return provider bodies or credentials."""
from __future__ import annotations

import asyncio
import json

import httpx

from backend.errors import ResourceValidationError
from backend.security.model_connections import ConnectionEdit, ModelConnectionStore


async def discover_models(edit: ConnectionEdit, store: ModelConnectionStore) -> dict:
    if edit.adapter not in {"openai", "anthropic", "gemini"}:
        raise ResourceValidationError("This service requires manual model configuration.")
    key = store.discovery_key(edit)
    defaults = {"openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com", "gemini": "https://generativelanguage.googleapis.com/v1beta"}
    base = edit.base_url or defaults[edit.adapter]
    if edit.auth_mode == "none" and not edit.base_url:
        raise ResourceValidationError("Enter the local service address.")
    if edit.adapter == "anthropic":
        url = base + ("/models" if base.endswith("/v1") else "/v1/models")
        headers = {"anthropic-version": "2023-06-01", **({"x-api-key": key} if key else {})}
        params = {"limit": "100"}
    elif edit.adapter == "gemini":
        url = base + "/models"
        headers = {"x-goog-api-key": key} if key else {}
        params = {"pageSize": "100"}
    else:
        url = base + "/models"
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        params = {}
    found: dict[str, dict] = {}
    truncated = False
    try:
        async with asyncio.timeout(15), httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            for page in range(5):
                async with client.stream("GET", url, headers=headers, params=params) as response:
                    if response.status_code in {401, 403}:
                        raise ResourceValidationError("The service rejected the credentials. Check the API key and account access.")
                    if response.status_code == 429:
                        raise ResourceValidationError("The service is busy or rate limited. Try again later.")
                    if response.status_code != 200:
                        raise ResourceValidationError("The service could not list models. Check the address or add a model manually.")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 2 * 1024 * 1024:
                            raise ResourceValidationError("The model list is too large. Add a model manually.")
                data = json.loads(body)
                rows = data.get("models" if edit.adapter == "gemini" else "data", [])
                if not isinstance(rows, list):
                    raise ValueError("Invalid model list")
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    if edit.adapter == "gemini" and "generateContent" not in row.get("supportedGenerationMethods", []):
                        continue
                    identifier = row.get("name" if edit.adapter == "gemini" else "id")
                    if not isinstance(identifier, str):
                        continue
                    identifier = identifier.removeprefix("models/") if edit.adapter == "gemini" else identifier
                    if not identifier.strip() or len(identifier) > 200:
                        continue
                    label = row.get("displayName") or row.get("display_name") or identifier
                    found[identifier] = {"id": identifier, "name": str(label)[:120]}
                    if len(found) >= 500:
                        truncated = True
                        break
                token = data.get("nextPageToken") if edit.adapter == "gemini" else data.get("last_id") if data.get("has_more") else None
                if not token or truncated:
                    break
                if page == 4:
                    truncated = True
                params["pageToken" if edit.adapter == "gemini" else "after_id"] = str(token)
    except (httpx.HTTPError, TimeoutError):
        raise ResourceValidationError("Could not reach the model service. Check its address and network, then retry.") from None
    except (ValueError, TypeError, AttributeError):
        raise ResourceValidationError("The service returned an unsupported model list. Add a model manually.") from None
    return {"models": sorted(found.values(), key=lambda m: m["id"]), "truncated": truncated}
