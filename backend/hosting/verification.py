"""Email and SMS verification code delivery for hosted authentication.

Supports production providers (SMTP, Aliyun Dysms, HTTP webhooks) and an
operator-local ``log`` mode for development before external credentials are ready.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import secrets
import smtplib
from urllib.parse import quote
import uuid

import httpx
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)


class VerificationDeliveryError(Exception):
    """The configured email or SMS provider rejected or failed delivery."""


def _aliyun_percent_encode(value: str) -> str:
    return quote(str(value), safe="~")


def sign_aliyun_rpc(params: dict[str, str], access_key_secret: str, method: str = "POST") -> dict[str, str]:
    signed = dict(params)
    canonical = "&".join(f"{_aliyun_percent_encode(k)}={_aliyun_percent_encode(signed[k])}" for k in sorted(signed))
    string_to_sign = f"{method.upper()}&%2F&{_aliyun_percent_encode(canonical)}"
    digest = hmac.digest((access_key_secret + "&").encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha1)
    signed["Signature"] = base64.b64encode(digest).decode("ascii")
    return signed


@dataclass(frozen=True)
class VerificationSettings:
    email_mode: str = "log"
    sms_mode: str = "log"
    smtp_host: str | None = None
    smtp_port: int = 465
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_sender: str | None = None
    smtp_starttls: bool = False
    aliyun_access_key_id: str | None = None
    aliyun_access_key_secret: str | None = None
    aliyun_sign_name: str | None = None
    aliyun_template_code: str | None = None
    aliyun_template_param: str = "code"
    aliyun_endpoint: str = "https://dysmsapi.aliyuncs.com/"
    email_webhook_url: str | None = None
    email_webhook_token: str | None = None
    sms_webhook_url: str | None = None
    sms_webhook_token: str | None = None
    log_path: Path | None = None

    def __post_init__(self):
        if self.email_mode not in {"log", "smtp", "webhook"}:
            raise ValueError("email_mode must be log, smtp, or webhook")
        if self.sms_mode not in {"log", "aliyun", "webhook"}:
            raise ValueError("sms_mode must be log, aliyun, or webhook")
        if self.email_mode == "smtp" and (not self.smtp_host or not self.smtp_sender):
            raise ValueError("SMTP email delivery requires smtp_host and smtp_sender")
        if self.sms_mode == "aliyun" and not all((
            self.aliyun_access_key_id, self.aliyun_access_key_secret,
            self.aliyun_sign_name, self.aliyun_template_code,
        )):
            raise ValueError("Aliyun SMS delivery requires access key, sign_name, and template_code")
        if self.email_mode == "webhook" and not self.email_webhook_url:
            raise ValueError("Email webhook delivery requires email_webhook_url")
        if self.sms_mode == "webhook" and not self.sms_webhook_url:
            raise ValueError("SMS webhook delivery requires sms_webhook_url")


def read_verification_settings(path: Path) -> VerificationSettings:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Invalid verification configuration")
    raw = dict(raw)
    if "log_path" in raw and raw["log_path"] is not None:
        log_path = Path(raw["log_path"])
        raw["log_path"] = log_path if log_path.is_absolute() else (path.parent / log_path)
    return VerificationSettings(**raw)


class VerificationDispatcher:
    def __init__(self, settings: VerificationSettings | None = None, *, transport=None):
        self.settings = settings or VerificationSettings()
        self.transport = transport

    async def send(self, channel: str, target: str, code: str, purpose: str) -> None:
        if channel == "email":
            await self._send_email(target, code, purpose)
        elif channel == "sms":
            await self._send_sms(target, code, purpose)
        else:
            raise ValueError("Unsupported verification channel")

    def _write_dev_log(self, channel: str, target: str, code: str, purpose: str) -> None:
        logger.info("OAW dev verification code issued: channel=%s target=%s purpose=%s code=%s",
                    channel, target, purpose, code)
        if self.settings.log_path is not None:
            path = Path(self.settings.log_path)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
                timestamp = datetime.now(timezone.utc).isoformat()
                stream.write(json.dumps({
                    "ts": timestamp, "channel": channel, "target": target, "purpose": purpose, "code": code,
                }, ensure_ascii=False) + "\n")

    def _send_smtp_sync(self, target: str, code: str) -> None:
        from email.utils import formataddr
        cfg = self.settings
        msg = EmailMessage()
        msg["Subject"] = f"Open Agent World 验证码：{code}"
        msg["From"] = formataddr(("Open Agent World", cfg.smtp_sender or ""))
        msg["To"] = target
        msg.set_content(
            f"您的 Open Agent World 验证码为：{code}\n\n该验证码 5 分钟内有效，如非本人操作请忽略本邮件。"
        )
        client = None
        try:
            if cfg.smtp_starttls:
                client = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=12)
                client.ehlo()
                client.starttls()
                client.ehlo()
            else:
                client = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=12)
            if cfg.smtp_username and cfg.smtp_password:
                client.login(cfg.smtp_username, cfg.smtp_password)
            client.send_message(msg)
        except (OSError, smtplib.SMTPException) as exc:
            raise VerificationDeliveryError("Could not send verification email") from exc
        finally:
            if client is not None:
                # Providers such as smtp.qq.com may close TLS immediately after
                # accepting DATA, causing QUIT to raise (-1, b'\x00\x00\x00').
                try:
                    client.quit()
                except (OSError, smtplib.SMTPException):
                    client.close()

    async def _send_email(self, target: str, code: str, purpose: str) -> None:
        mode = self.settings.email_mode
        if mode == "log":
            await run_in_threadpool(self._write_dev_log, "email", target, code, purpose)
            return
        if mode == "smtp":
            await run_in_threadpool(self._send_smtp_sync, target, code)
            return
        await self._post_webhook(self.settings.email_webhook_url, self.settings.email_webhook_token,
                                 {"channel": "email", "target": target, "code": code, "purpose": purpose})

    async def _send_sms(self, target: str, code: str, purpose: str) -> None:
        mode = self.settings.sms_mode
        if mode == "log":
            await run_in_threadpool(self._write_dev_log, "sms", target, code, purpose)
            return
        if mode == "aliyun":
            await self._send_aliyun_sms(target, code)
            return
        await self._post_webhook(self.settings.sms_webhook_url, self.settings.sms_webhook_token,
                                 {"channel": "sms", "target": target, "code": code, "purpose": purpose})

    async def _send_aliyun_sms(self, target: str, code: str) -> None:
        cfg = self.settings
        phone = target[3:] if target.startswith("+86") and len(target) == 14 else target.lstrip("+")
        params = {
            "AccessKeyId": cfg.aliyun_access_key_id or "",
            "Action": "SendSms",
            "Format": "JSON",
            "PhoneNumbers": phone,
            "RegionId": "cn-hangzhou",
            "SignName": cfg.aliyun_sign_name or "",
            "SignatureMethod": "HMAC-SHA1",
            "SignatureNonce": str(uuid.uuid4()),
            "SignatureVersion": "1.0",
            "TemplateCode": cfg.aliyun_template_code or "",
            "TemplateParam": json.dumps({cfg.aliyun_template_param: code}),
            "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "Version": "2017-05-25",
        }
        signed = sign_aliyun_rpc(params, cfg.aliyun_access_key_secret or "", "POST")
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=10, trust_env=False) as client:
                response = await client.post(cfg.aliyun_endpoint, data=signed)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise VerificationDeliveryError("Could not send SMS verification code") from exc
        if payload.get("Code") != "OK":
            logger.warning("Aliyun SMS rejected request: code=%s", payload.get("Code"))
            raise VerificationDeliveryError("SMS provider rejected the delivery request")

    async def _post_webhook(self, url: str | None, token: str | None, payload: dict) -> None:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=10, trust_env=False) as client:
                response = await client.post(url or "", json=payload, headers=headers)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise VerificationDeliveryError("Verification webhook delivery failed") from exc
