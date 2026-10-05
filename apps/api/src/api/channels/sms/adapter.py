"""Outbound client for SMS Gate (sms-gate.app); Cloud and Local differ only in URL and creds."""

from __future__ import annotations

import httpx

from api.core.http import http_request

_SEND_TIMEOUT_SECONDS = 15.0


class SmsGatewayAdapter:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        sim_number: int | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._auth = (username, password)
        self._sim = sim_number
        self._client = client

    async def send_text(self, to: str, body: str) -> None:
        """Newer SMS Gate builds take {"textMessage": {...}}, older ones a flat {"message": ...},
        so a 400/422 retries with the flat shape. simNumber (1-based) must be the SIM with
        international credit or non-domestic replies fail at the carrier.
        """
        url = f"{self._base}/message"
        sim: dict[str, object] = {"simNumber": self._sim} if self._sim is not None else {}
        response = await self._post(
            url, {"textMessage": {"text": body}, "phoneNumbers": [to], **sim}
        )
        if response.status_code in (400, 422):
            response = await self._post(url, {"message": body, "phoneNumbers": [to], **sim})
        if response.status_code >= 300:
            raise RuntimeError(f"SMS Gate send failed ({response.status_code}): {response.text}")

    async def _post(self, url: str, payload: dict[str, object]) -> httpx.Response:
        return await http_request(
            self._client,
            "POST",
            url,
            timeout=_SEND_TIMEOUT_SECONDS,
            json=payload,
            auth=self._auth,
        )
