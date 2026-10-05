"""Closed value sets and free-text caps shared by every channel.

Values are canonical English on the wire; channels localize only the display
copy. Tuple order is the order of the numbered menus.
"""

from __future__ import annotations

from typing import TypeGuard, get_args

from api.schemas.common import DamageClass, Debris

DAMAGE_VALUES: tuple[DamageClass, ...] = get_args(DamageClass)
DEBRIS_VALUES: tuple[Debris, ...] = get_args(Debris)

# Mirrors `apps/api/src/api/whatsapp/flows/report_v1.json` ``INFRA_TYPE``.
INFRA_TYPES: tuple[str, ...] = (
    "residential",
    "commercial",
    "government",
    "utility",
    "transport",
    "community",
    "public_spaces",
    "other",
)

# The PWA's StepInfraDetails values. ``NATURE_OTHER`` is the free-text escape hatch.
NATURE_LABELS: tuple[str, ...] = (
    "Earthquake",
    "Flood",
    "Tsunami",
    "Hurricane",
    "Landslide",
    "Wildfire",
    "Explosion",
    "Chemical incident",
    "Conflict",
    "Civil unrest",
)
NATURE_OTHER = "Other"

# Mirrors `apps/pwa/src/components/steps/StepInfraDetails.tsx` TYPE_OPTIONS.
CRISIS_NATURE_BUCKETS: dict[str, str] = {
    "Earthquake": "natural_hazards",
    "Flood": "natural_hazards",
    "Tsunami": "natural_hazards",
    "Hurricane": "natural_hazards",
    "Landslide": "natural_hazards",
    "Wildfire": "natural_hazards",
    "Explosion": "technological",
    "Chemical incident": "technological",
    "Conflict": "human_made",
    "Civil unrest": "human_made",
}

# English display is the key itself, so it is omitted.
NATURE_DISPLAY: dict[str, dict[str, str]] = {
    "Earthquake": {
        "ar": "زلزال",
        "es": "Terremoto",
        "fr": "Tremblement de terre",
        "ru": "Землетрясение",
        "zh": "地震",
    },
    "Flood": {
        "ar": "فيضان",
        "es": "Inundación",
        "fr": "Inondation",
        "ru": "Наводнение",
        "zh": "洪水",
    },
    "Tsunami": {
        "ar": "تسونامي",
        "es": "Tsunami",
        "fr": "Tsunami",
        "ru": "Цунами",
        "zh": "海啸",
    },
    "Hurricane": {
        "ar": "إعصار",
        "es": "Huracán",
        "fr": "Ouragan",
        "ru": "Ураган",
        "zh": "飓风",
    },
    "Landslide": {
        "ar": "انزلاق أرضي",
        "es": "Deslizamiento de tierra",
        "fr": "Glissement de terrain",
        "ru": "Оползень",
        "zh": "山体滑坡",
    },
    "Wildfire": {
        "ar": "حريق هائل",
        "es": "Incendio forestal",
        "fr": "Incendie de forêt",
        "ru": "Лесной пожар",
        "zh": "野火",
    },
    "Explosion": {
        "ar": "انفجار",
        "es": "Explosión",
        "fr": "Explosion",
        "ru": "Взрыв",
        "zh": "爆炸",
    },
    "Chemical incident": {
        "ar": "حادث كيميائي",
        "es": "Incidente químico",
        "fr": "Incident chimique",
        "ru": "Химический инцидент",
        "zh": "化学事故",
    },
    "Conflict": {
        "ar": "نزاع",
        "es": "Conflicto",
        "fr": "Conflit",
        "ru": "Конфликт",
        "zh": "冲突",
    },
    "Civil unrest": {
        "ar": "اضطرابات مدنية",
        "es": "Disturbios civiles",
        "fr": "Troubles civils",
        "ru": "Гражданские беспорядки",
        "zh": "内乱",
    },
}


def nature_label(value: str, lang: str) -> str:
    return NATURE_DISPLAY.get(value, {}).get(lang, value)


def is_damage_class(value: object) -> TypeGuard[DamageClass]:
    return value in DAMAGE_VALUES


def is_debris(value: object) -> TypeGuard[Debris]:
    return value in DEBRIS_VALUES


# ``MAX_ROUTE_CHARS`` must match ``schemas/reports.py`` (``max_length=1000``).
MAX_DESCRIPTION_CHARS = 1024
MAX_OTHER_TEXT_CHARS = 256
MAX_ROUTE_CHARS = 1000
