"""Generate an RSA-2048 keypair for the WhatsApp Flow data-exchange endpoint.

Python port of Meta's reference `keyGenerator.js`. Run:

    cd apps/api && PYTHONPATH=src uv run python -m api.channels.whatsapp.flow_keygen "<passphrase>"

Paste PRIVATE_KEY and PASSPHRASE into .env. Upload PUBLIC_KEY to Meta:

    curl -X POST \\
      "https://graph.facebook.com/v22.0/{phone_number_id}/whatsapp_business_encryption" \\
      -H "Authorization: Bearer $META_WHATSAPP_ACCESS_TOKEN" \\
      --data-urlencode "business_public_key=$(cat public.pem)"
"""

from __future__ import annotations

import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1]:
        print(
            'Passphrase is empty. Run: python -m api.channels.whatsapp.flow_keygen "<passphrase>"',
            file=sys.stderr,
        )
        return 2
    passphrase = sys.argv[1].encode("utf-8")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.BestAvailableEncryption(passphrase),
    ).decode("utf-8")
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )

    print("************* COPY PASSPHRASE & PRIVATE KEY BELOW TO .env *************")
    print(f'WHATSAPP_FLOW_PRIVATE_KEY_PASSPHRASE="{sys.argv[1]}"')
    print()
    # Quote with double quotes so the multi-line PEM stays one env var.
    # pydantic-settings handles quoted multiline values.
    print(f'WHATSAPP_FLOW_PRIVATE_KEY_PEM="{private_pem}"')
    print("************* COPY PASSPHRASE & PRIVATE KEY ABOVE TO .env *************")
    print()
    print("************* COPY PUBLIC KEY BELOW (upload to Meta) *************")
    print(public_pem)
    print("************* COPY PUBLIC KEY ABOVE *************")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
