"""Application settings, loaded from the repo-root .env (see .env.example).

Defaults suit native dev against the Compose stack; Compose overrides the
connection settings with container addresses.
"""

from __future__ import annotations

import base64
import json
from functools import lru_cache
from pathlib import Path
from typing import Self, cast

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_env_file() -> Path:
    # Native dev finds the repo .env (legacy: infra/.env); in containers Compose
    # injects the vars instead.
    for parent in Path(__file__).resolve().parents:
        if (parent / "compose.yaml").is_file() and (parent / ".env").is_file():
            return parent / ".env"
        if (parent / "infra" / ".env").is_file():
            return parent / "infra" / ".env"
    return Path("/nonexistent/.env")


_ENV = _find_env_file()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Public URL of the site. Derives the privacy-policy link and cookie security.
    site_url: str = "http://localhost:8080"

    # Empty builds it from api_service_db_password against the local DB port.
    database_url: str = ""
    api_service_db_password: str = "api_service_dev_password"
    supabase_url: str = "http://127.0.0.1:54321"
    # Browser-reachable Supabase URL embedded in responses; supabase_url may be
    # an in-network address (e.g. the Kong container) that browsers cannot reach.
    # Empty means supabase_url.
    supabase_public_url: str = ""
    # Falls back to SERVICE_ROLE_KEY, the name the Supabase stack uses.
    supabase_service_role_key: str = ""
    service_role_key: str = ""
    supabase_storage_bucket: str = "report-photos"
    # Private buckets for analysis-report PDFs and photo-export zip parts.
    crisis_reports_bucket: str = "crisis-reports"
    report_exports_bucket: str = "report-exports"
    # Photo export: a zip part closes at export_part_size_bytes; a job starts only if
    # free disk exceeds the size estimate plus export_disk_headroom_bytes.
    export_part_size_bytes: int = 2 * 1024 * 1024 * 1024
    export_disk_headroom_bytes: int = 20 * 1024 * 1024 * 1024
    export_signed_url_ttl_seconds: int = 15 * 60
    export_retention_hours: int = 48
    # Staging dir for export parts (None = system temp). Point it at the storage volume
    # when temp is on another filesystem, since the disk guard measures this dir.
    export_scratch_dir: str | None = None
    reserved_crisis_name: str = "Other / Unspecified"

    redis_dsn: str = "redis://127.0.0.1:6379/0"

    # Concurrent arq jobs per worker (mostly I/O-bound). Keep at or below the worker
    # DB pool ceiling (30, see make_engine); scale out with replicas instead.
    worker_max_jobs: int = 20

    # Building-ingest estimate rates, fit to observed runs on the deployment box.
    ingest_est_download_rows_per_sec: float = 12_000.0
    ingest_est_load_rows_per_sec: float = 5_000.0

    # Days after a crisis is archived before the retention cron deletes its reports.
    report_retention_days: int = 90

    # Coordinator auth. Token TTLs are fixed in code.
    supabase_jwt_audience: str = "authenticated"
    # None derives it as {supabase_url}/auth/v1.
    supabase_jwt_issuer: str | None = None

    auth_refresh_cookie_name: str = "rid_refresh"
    # Unset means True when site_url is https.
    auth_refresh_cookie_secure: bool = False
    # "lax" | "strict" | "none"; "none" (cross-site PWA) also requires secure=True.
    auth_refresh_cookie_samesite: str = "lax"
    # Behind Caddy, which strips /api, production needs /api/auth or the cookie is never sent.
    auth_refresh_cookie_path: str = "/auth"

    # When both are set, startup creates or resyncs this admin in Supabase Auth.
    # Setting only one disables bootstrap.
    admin_email: str | None = None
    admin_password: str | None = None

    # WhatsApp bot (Twilio + Azure OpenAI). /whatsapp/* returns 503 until configured.
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = ""  # e.g. "whatsapp:+14155238886"
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""  # e.g. "https://my-resource.openai.azure.com/"
    azure_openai_api_version: str = "2024-10-21"
    # Azure deployment name, not the model id.
    whatsapp_llm_deployment: str = ""
    # URL Twilio signs against; empty uses the request URL.
    whatsapp_public_url: str = ""
    # Dev: reply with a canned message, skipping the LLM and flow.
    whatsapp_stub_reply: bool = False

    # OpenAI-compatible AI endpoints. A role is on once its model is set; its URL
    # and key default to ai_base_url / ai_api_key. Disabled roles make worker jobs
    # mark rows failed or skip, and routes return 503.
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_llm_base_url: str = ""
    ai_llm_api_key: str = ""
    ai_llm_model: str = ""
    ai_vision_base_url: str = ""
    ai_vision_api_key: str = ""
    ai_vision_model: str = ""
    # Served LoRA name for captioning; reuses the AI_VISION_* endpoint, with
    # AI_VISION_MODEL as the LoRA's base model.
    ai_vision_disaster_model: str = ""
    ai_embedding_base_url: str = ""
    ai_embedding_api_key: str = ""
    ai_embedding_model: str = ""
    # Small multimodal model behind POST /ai/classify-damage.
    ai_classifier_base_url: str = ""
    ai_classifier_api_key: str = ""
    ai_classifier_model: str = ""
    # Speech-to-text model behind POST /ai/transcribe.
    ai_transcription_base_url: str = ""
    ai_transcription_api_key: str = ""
    ai_transcription_model: str = ""

    # Meta WhatsApp Cloud API; when set, /whatsapp/webhook/meta is also served.
    meta_whatsapp_phone_number_id: str = ""
    meta_whatsapp_access_token: str = ""
    meta_whatsapp_app_secret: str = ""
    meta_whatsapp_verify_token: str = ""  # must match the webhook config in Meta
    meta_whatsapp_graph_version: str = "v22.0"

    # Ingestion mode for citizen damage reports over WhatsApp.
    #   * "template" → deterministic chat walker using interactive list /
    #                  button messages. Meta only, no LLM dependency.
    #   * anything else (incl. empty) → "llm": free-text conversation parsed
    #                  by Azure OpenAI.
    # There is no "flow" mode: the bot never sends the Meta WhatsApp Flow
    # (report_v1.json). It is only sent manually via
    # scripts/whatsapp/meta-send-flow.sh, and its completion (`nfm_reply`)
    # is only handled in "llm" mode — the template walker ignores it.
    whatsapp_report_mode: str = "template"

    # SMS Gate (sms-gate.app) Android gateway; outbound uses HTTP Basic auth.
    sms_gateway_base_url: str = "https://api.sms-gate.app/3rdparty/v1"
    sms_gateway_username: str = ""
    sms_gateway_password: str = ""
    # 1-based SIM slot for outbound; must be the SIM with international SMS credit.
    # None uses the gateway's default SIM.
    sms_gateway_sim: int | None = 2
    # HMAC signing key for /sms/webhook. Empty rejects every inbound request with 401.
    sms_webhook_secret: str = ""

    # IVR over Twilio Voice, reusing the Twilio credentials above. /ivr/* returns 503
    # until twilio_voice_number is set.
    twilio_voice_number: str = ""  # e.g. "+15017122661"
    # URL Twilio signs against; empty uses the request URL.
    ivr_public_url: str = ""

    # Sent in the opening message of the text channels (SMS, WhatsApp); IVR speaks
    # a short notice without the URL. Empty means {site_url}/legal/privacy-policy.
    privacy_policy_url: str = ""

    # WhatsApp Flow data-exchange key (python -m api.channels.whatsapp.flow_keygen).
    # Empty makes /whatsapp/flow/data-exchange return 503.
    whatsapp_flow_private_key_pem: str = ""
    whatsapp_flow_private_key_passphrase: str = ""

    whatsapp_flow_id: str = ""
    # "draft" only delivers to the app's test recipients; use "published" in production.
    whatsapp_flow_mode: str = "draft"

    # Passed to allow_origin_regex. Native apps call cross-origin, so a production
    # value must admit capacitor://localhost (iOS) and https://localhost (Android).
    cors_origin_regex: str = r".*"

    @model_validator(mode="after")
    def _derive(self) -> Self:
        site = self.site_url.rstrip("/")
        if not self.database_url:
            self.database_url = (
                "postgresql+asyncpg://api_service:"
                f"{self.api_service_db_password}@127.0.0.1:54322/postgres"
            )
        self.supabase_public_url = self.supabase_public_url or self.supabase_url
        self.supabase_service_role_key = self.supabase_service_role_key or self.service_role_key
        if "auth_refresh_cookie_secure" not in self.model_fields_set:
            self.auth_refresh_cookie_secure = site.startswith("https://")
        self.privacy_policy_url = self.privacy_policy_url or f"{site}/legal/privacy-policy"
        for role in ("llm", "vision", "embedding", "classifier", "transcription"):
            for part, shared in (("base_url", self.ai_base_url), ("api_key", self.ai_api_key)):
                name = f"ai_{role}_{part}"
                if not getattr(self, name):
                    setattr(self, name, shared)
        return self

    def uses_demo_keys(self) -> bool:
        """True when the service-role key is Supabase's published demo key."""
        try:
            payload = self.supabase_service_role_key.split(".")[1]
            claims: object = json.loads(
                base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
            )
        except (IndexError, ValueError):
            return False
        return (
            isinstance(claims, dict)
            and cast(dict[str, object], claims).get("iss") == "supabase-demo"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]
