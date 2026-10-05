"""WhatsApp provider adapters (Twilio, Meta Cloud API): the only code that talks to a provider.

Replies always go out over REST, never TwiML on the webhook response, because
interactive lists and buttons can't be sent via TwiML; plain text uses the same
path so there is one outbound code path.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from typing import Protocol

import httpx

from api.core.http import http_request

# Meta WhatsApp Cloud API platform limits.
LIST_BUTTON_MAX = 20
LIST_ROW_TITLE_MAX = 24
LIST_MAX_ROWS = 10
BUTTON_TITLE_MAX = 20
MAX_BUTTONS = 3


@dataclass(frozen=True)
class ListRow:
    id: str
    title: str  # WhatsApp max 24 chars
    description: str | None = None  # max 72 chars


@dataclass(frozen=True)
class Button:
    id: str
    title: str  # WhatsApp max 20 chars, 3 buttons per message


class Provider(Protocol):
    """Outbound surface; signature checks stay on each adapter as providers sign differently."""

    async def send_text(self, to: str, body: str) -> None: ...

    async def send_list(
        self,
        to: str,
        body: str,
        button: str,
        rows: list[ListRow],
    ) -> None: ...

    async def send_buttons(
        self,
        to: str,
        body: str,
        buttons: list[Button],
    ) -> None: ...

    async def send_flow(
        self,
        to: str,
        *,
        flow_id: str,
        flow_token: str,
        body: str,
        cta: str,
        first_screen: str,
        mode: str = "draft",
        header: str | None = None,
        footer: str | None = None,
    ) -> None: ...

    async def send_location_request(self, to: str, body: str) -> None: ...


class TwilioAdapter:
    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._from = from_
        self._client = client
        self._base = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}"

    async def send_text(self, to: str, body: str) -> None:
        await self._post_message({"To": to, "From": self._from, "Body": body})

    async def send_list(
        self,
        to: str,
        body: str,
        button: str,
        rows: list[ListRow],
    ) -> None:
        # Sandbox can't send interactive messages without a Content template;
        # render as numbered/bulleted text instead.
        rendered = (
            body
            + "\n\n"
            + "\n".join(
                f"{i + 1}. {row.title}" + (f" — {row.description}" if row.description else "")
                for i, row in enumerate(rows)
            )
            + f"\n\nReply with the number for: {button}"
        )
        await self.send_text(to, rendered)

    async def send_buttons(
        self,
        to: str,
        body: str,
        buttons: list[Button],
    ) -> None:
        # Same sandbox limitation as send_list.
        rendered = body + "\n\n" + "  •  ".join(b.title for b in buttons)
        await self.send_text(to, rendered)

    async def send_flow(
        self,
        to: str,
        *,
        flow_id: str,
        flow_token: str,
        body: str,
        cta: str,
        first_screen: str,
        mode: str = "draft",
        header: str | None = None,
        footer: str | None = None,
    ) -> None:
        raise NotImplementedError(
            "send_flow is not supported on the Twilio adapter; "
            "set WHATSAPP_REPORT_MODE=llm or switch to the Meta provider."
        )

    async def send_location_request(self, to: str, body: str) -> None:
        raise NotImplementedError(
            "send_location_request is not supported on the Twilio adapter; "
            "switch to the Meta provider."
        )

    async def _post_message(self, data: dict[str, str]) -> None:
        # Sessions store bare E.164; Twilio needs To on the same channel as From.
        to = data.get("To", "")
        if to and not to.startswith("whatsapp:"):
            data = {**data, "To": f"whatsapp:{to}"}
        url = f"{self._base}/Messages.json"
        auth = (self._account_sid, self._auth_token)
        response = await http_request(self._client, "POST", url, timeout=15.0, data=data, auth=auth)
        if response.status_code >= 300:
            raise RuntimeError(f"Twilio send failed ({response.status_code}): {response.text}")

    def validate_signature(
        self,
        url: str,
        params: dict[str, str],
        signature: str,
    ) -> bool:
        expected = self._compute_signature(url, params)
        return hmac.compare_digest(expected, signature)

    def _compute_signature(self, url: str, params: dict[str, str]) -> str:
        # URL plus each param name+value sorted by name, HMAC-SHA1, base64.
        # https://www.twilio.com/docs/usage/security
        payload = url
        for key in sorted(params.keys()):
            payload += key + params[key]
        digest = hmac.new(
            self._auth_token.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        return base64.b64encode(digest).decode("ascii")


class MetaCloudAdapter:
    def __init__(
        self,
        *,
        phone_number_id: str,
        access_token: str,
        app_secret: str,
        verify_token: str,
        graph_version: str = "v22.0",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._phone_number_id = phone_number_id
        self._access_token = access_token
        self._app_secret = app_secret
        self._verify_token = verify_token
        self._client = client
        self._base = f"https://graph.facebook.com/{graph_version}"

    async def send_text(self, to: str, body: str) -> None:
        await self._post_message(to, "text", {"preview_url": False, "body": body})

    async def send_list(
        self,
        to: str,
        body: str,
        button: str,
        rows: list[ListRow],
    ) -> None:
        await self._post_message(
            to,
            "interactive",
            {
                "type": "list",
                "body": {"text": body},
                "action": {
                    "button": button[:LIST_BUTTON_MAX],
                    "sections": [
                        {
                            "title": "",
                            "rows": [
                                {
                                    "id": r.id,
                                    "title": r.title[:LIST_ROW_TITLE_MAX],
                                    **({"description": r.description} if r.description else {}),
                                }
                                for r in rows[:LIST_MAX_ROWS]
                            ],
                        }
                    ],
                },
            },
        )

    async def send_buttons(
        self,
        to: str,
        body: str,
        buttons: list[Button],
    ) -> None:
        await self._post_message(
            to,
            "interactive",
            {
                "type": "button",
                "body": {"text": body},
                "action": {
                    "buttons": [
                        {
                            "type": "reply",
                            "reply": {"id": b.id, "title": b.title[:BUTTON_TITLE_MAX]},
                        }
                        for b in buttons[:MAX_BUTTONS]
                    ],
                },
            },
        )

    async def send_flow(
        self,
        to: str,
        *,
        flow_id: str,
        flow_token: str,
        body: str,
        cta: str,
        first_screen: str,
        mode: str = "draft",
        header: str | None = None,
        footer: str | None = None,
    ) -> None:
        """Send a WhatsApp Flow message.

        ``mode="draft"`` delivers only to the app's test recipients and needs no
        Business Verification. Meta echoes ``flow_token`` back in the completion
        payload, so it should be unique per send.
        """
        interactive: dict[str, object] = {
            "type": "flow",
            "body": {"text": body},
            "action": {
                "name": "flow",
                "parameters": {
                    "flow_message_version": "3",
                    "flow_token": flow_token,
                    "flow_id": flow_id,
                    "flow_cta": cta[:20],
                    "flow_action": "navigate",
                    "mode": mode,
                    # Meta rejects an empty `data: {}`, so omit it.
                    "flow_action_payload": {"screen": first_screen},
                },
            },
        }
        if header:
            interactive["header"] = {"type": "text", "text": header[:60]}
        if footer:
            interactive["footer"] = {"text": footer[:60]}

        await self._post_message(to, "interactive", interactive)

    async def send_location_request(self, to: str, body: str) -> None:
        """Ask for a location via the native picker; the reply is a normal location message."""
        await self._post_message(
            to,
            "interactive",
            {
                "type": "location_request_message",
                "body": {"text": body},
                "action": {"name": "send_location"},
            },
        )

    async def _post_message(self, to: str, type_: str, content: dict[str, object]) -> None:
        body: dict[str, object] = {
            "messaging_product": "whatsapp",
            "to": to.lstrip("+"),
            "type": type_,
            type_: content,
        }
        url = f"{self._base}/{self._phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }
        response = await http_request(
            self._client, "POST", url, timeout=15.0, json=body, headers=headers
        )
        if response.status_code >= 300:
            raise RuntimeError(f"Meta send failed ({response.status_code}): {response.text}")

    def verify_subscription(self, mode: str, token: str, challenge: str) -> str | None:
        if mode == "subscribe" and token and token == self._verify_token:
            return challenge
        return None

    def validate_signature(self, raw_body: bytes, signature_header: str | None) -> bool:
        """Verify ``X-Hub-Signature-256`` (HMAC-SHA256 of the raw body with the app secret)."""
        if not signature_header or not signature_header.startswith("sha256="):
            return False
        expected = hmac.new(
            self._app_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        provided = signature_header[len("sha256=") :]
        return hmac.compare_digest(expected, provided)

    async def fetch_media_bytes(self, media_id: str) -> tuple[bytes, str | None]:
        """Return ``(bytes, mime)``; the signed media URL still needs the Bearer token."""
        signed_url = await self.fetch_media_url(media_id)
        headers = {"Authorization": f"Bearer {self._access_token}"}
        response = await http_request(
            self._client, "GET", signed_url, timeout=30.0, headers=headers
        )
        if response.status_code >= 300:
            raise RuntimeError(
                f"Meta media download failed ({response.status_code}): {response.text}"
            )
        mime = response.headers.get("content-type")
        return response.content, mime

    async def fetch_media_url(self, media_id: str) -> str:
        """Resolve an inbound media id to a short-lived signed URL."""
        url = f"{self._base}/{media_id}"
        headers = {"Authorization": f"Bearer {self._access_token}"}
        response = await http_request(self._client, "GET", url, timeout=15.0, headers=headers)
        if response.status_code >= 300:
            raise RuntimeError(
                f"Meta media lookup failed ({response.status_code}): {response.text}"
            )
        payload = response.json()
        media_url = payload.get("url")
        if not isinstance(media_url, str):
            raise RuntimeError(f"Meta media response missing url: {payload!r}")
        return media_url
