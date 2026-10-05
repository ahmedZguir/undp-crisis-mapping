"""Business logic for the WhatsApp Flow data-exchange endpoint.

Maps decrypted request bodies → response payloads per the contract in
`flows/report_v1.json`. Pure function, no I/O — easy to unit test.

Reference shape of `decrypted_body`:
    {
      "version": "3.0",
      "action": "ping" | "INIT" | "data_exchange" | "BACK",
      "screen": "<current screen id>",
      "data": { ...form fields + carried screen data... },
      "flow_token": "<opaque token we minted when sending the flow>"
    }

Response shape (encrypted before sending back):
    { "screen": "<next>", "data": { ... } }
    or for ping: { "data": { "status": "active" } }
    or to complete: { "screen": "SUCCESS",
                      "data": { "extension_message_response":
                                  { "params": { "flow_token": "..." } } } }
"""

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger(__name__)


def get_next_screen(decrypted_body: dict[str, Any]) -> dict[str, Any]:
    action = decrypted_body.get("action")
    screen = decrypted_body.get("screen")
    raw_data = decrypted_body.get("data")
    data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}  # pyright: ignore[reportUnknownVariableType]

    # 1. Health check — Meta pings the endpoint to confirm it's alive.
    if action == "ping":
        return {"data": {"status": "active"}}

    # 2. Client-side error notification. Ack and move on; Meta won't retry.
    if data.get("error"):
        _logger.warning("whatsapp.flow.client_error data=%r", data)
        return {"data": {"acknowledged": True}}

    # 3. INIT — first hit when the flow opens. report_v1.json starts at
    # PHOTO with no carried data, so we return its empty data dict.
    if action == "INIT":
        return {"screen": "PHOTO", "data": {}}

    # 4. data_exchange — server-side branching. report_v1.json only uses
    # this once: after PHOTO's "Next" footer, carrying photo + damage_class
    # to the DESCRIPTION screen.
    if action == "data_exchange":
        if screen == "PHOTO":
            # `data.photo` is a list of media descriptors here. We don't
            # need to surface it onto the next screen — it stays in the
            # form state and is delivered with the terminal `complete`
            # payload. DESCRIPTION only needs damage_class.
            damage_class = data.get("damage_class", "")
            return {
                "screen": "DESCRIPTION",
                "data": {"damage_class": damage_class},
            }

        _logger.warning("whatsapp.flow.data_exchange_unhandled_screen screen=%s", screen)

    # Fall-through: unhandled action/screen — raise so the route answers 500.
    _logger.error("whatsapp.flow.unhandled_request action=%s screen=%s", action, screen)
    raise ValueError(f"unhandled_flow_request action={action!r} screen={screen!r}")


__all__ = ["get_next_screen"]
