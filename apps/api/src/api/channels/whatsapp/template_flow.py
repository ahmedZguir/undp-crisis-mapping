"""Deterministic WhatsApp report flow using interactive lists and reply buttons.

No LLM. Meta provider only, since Twilio renders interactive messages as plain
text. The current step lives in ``session.scratch["template_step"]``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal, cast

from api.channels.languages import (
    LANGUAGE_NATIVE_NAMES,
    SUPPORTED_LANGS,
    Lang,
    normalize_lang,
    resolve_strings,
)
from api.channels.plan import Slot, plan_from_schema
from api.channels.sessions import LanguagePrefs, Session, SessionStore
from api.channels.submitter import ReportSubmitter
from api.channels.values import (
    INFRA_TYPES,
    MAX_DESCRIPTION_CHARS,
    MAX_OTHER_TEXT_CHARS,
    MAX_ROUTE_CHARS,
    NATURE_LABELS,
    NATURE_OTHER,
    is_damage_class,
    is_debris,
)
from api.channels.whatsapp.adapter import (
    LIST_MAX_ROWS,
    LIST_ROW_TITLE_MAX,
    Button,
    ListRow,
    Provider,
)
from api.channels.whatsapp.flow import (
    InboundMessage,
    capture_photo,
    draft_complete,
    parse_reset,
)
from api.channels.whatsapp.messages import strings_for
from api.crises.service import CrisisService

_logger = logging.getLogger(__name__)

Step = Literal[
    "LANGUAGE",
    "CRISIS",
    "PHOTO",
    "DAMAGE",
    "DESCRIPTION",
    "DESCRIPTION_TEXT",
    "DEBRIS",
    "INFRA_TYPE",
    "INFRA_TYPE_OTHER",
    "CRISIS_NATURE",
    "CRISIS_NATURE_OTHER",
    "GENERIC",
    "LOCATION",
    "ROUTE",
    "REVIEW",
    "EDIT_MENU",
    "DONE",
]

# Everything after DAMAGE is generated from the crisis form schema.
_BUILTIN_STEP_BY_KIND: dict[str, Step] = {
    "description": "DESCRIPTION",
    "debris": "DEBRIS",
    "infra_type": "INFRA_TYPE",
    "crisis_nature": "CRISIS_NATURE",
    "location": "LOCATION",
}

_EDIT_STEP_BY_TOKEN: dict[str, Step] = {
    "photo": "PHOTO",
    "damage": "DAMAGE",
    **_BUILTIN_STEP_BY_KIND,
}

# One row in every list is reserved for "Start over".
_MAX_CONTENT_ROWS = LIST_MAX_ROWS - 1


def _restart_row(s: dict[str, str]) -> ListRow:
    return ListRow(id="restart", title=s["restart_row"])


def _hint(s: dict[str, str], body: str) -> str:
    """Append the restart hint; list steps use a restart row instead."""
    return f"{body}\n\n{s['restart_hint']}"


# A missing language falls back to English (relied on by the language picker).
_STRINGS: dict[str, dict[str, str]] = {
    "consent_notice": {
        "en": "By continuing you agree to our privacy policy: {privacy_url}",
        "ar": "بالمتابعة، أنت توافق على سياسة الخصوصية الخاصة بنا: {privacy_url}",
        "es": "Al continuar, acepta nuestra política de privacidad: {privacy_url}",
        "fr": "En continuant, vous acceptez notre politique de confidentialité : {privacy_url}",
        "ru": "Продолжая, вы соглашаетесь с нашей политикой конфиденциальности: {privacy_url}",
        "zh": "继续即表示您同意我们的隐私政策：{privacy_url}",
    },
    "intro": {
        "en": "Hello! This service helps you report building damage so responders can reach it. I'll guide you step by step. Just tap the options.",
        "ar": "مرحباً! تساعدك هذه الخدمة على الإبلاغ عن أضرار المباني ليصل إليها المستجيبون. سأرشدك خطوة بخطوة. فقط اضغط الخيارات.",
        "es": "¡Hola! Este servicio le ayuda a reportar daños en edificios para que los equipos de respuesta puedan llegar. Le guiaré paso a paso. Solo toque las opciones.",
        "fr": "Bonjour ! Ce service vous aide à signaler les dégâts aux bâtiments afin que les secours puissent intervenir. Je vous guiderai étape par étape. Appuyez simplement sur les options.",
        "ru": "Здравствуйте! Этот сервис помогает сообщить о повреждениях зданий, чтобы спасатели могли их найти. Я проведу вас шаг за шагом. Просто нажимайте варианты.",
        "zh": "您好！此服务帮助您报告建筑损害，以便救援人员能够到达。我将逐步引导您——只需点击选项即可。",
    },
    "restart_hint": {
        "en": "Type *restart* anytime to start a new report.",
        "ar": "اكتب *restart* في أي وقت لبدء بلاغ جديد.",
        "es": "Escriba *restart* en cualquier momento para iniciar un nuevo informe.",
        "fr": "Tapez *restart* à tout moment pour recommencer un signalement.",
        "ru": "Напишите *restart* в любой момент, чтобы начать новое сообщение.",
        "zh": "随时输入 *restart* 可开始新的报告。",
    },
    "ask_edit": {
        "en": "Which answer would you like to change?",
        "ar": "أي إجابة تريد تعديلها؟",
        "es": "¿Qué respuesta desea cambiar?",
        "fr": "Quelle réponse souhaitez-vous modifier ?",
        "ru": "Какой ответ вы хотите изменить?",
        "zh": "您想修改哪一项？",
    },
    "review_photo": {
        "en": "Photo",
        "ar": "الصورة",
        "es": "Foto",
        "fr": "Photo",
        "ru": "Фото",
        "zh": "照片",
    },
    "restart_row": {
        "en": "🔄 Start over",
        "ar": "🔄 ابدأ من جديد",
        "es": "🔄 Empezar de nuevo",
        "fr": "🔄 Recommencer",
        "ru": "🔄 Начать заново",
        "zh": "🔄 重新开始",
    },
    # Chrome around generic questions; labels come translated from the schema.
    "generic_button": {
        "en": "Choose",
        "ar": "اختر",
        "es": "Elegir",
        "fr": "Choisir",
        "ru": "Выбрать",
        "zh": "选择",
    },
    "generic_free_hint": {
        "en": "Type your answer.",
        "ar": "اكتب إجابتك.",
        "es": "Escriba su respuesta.",
        "fr": "Tapez votre réponse.",
        "ru": "Введите ваш ответ.",
        "zh": "请输入您的回答。",
    },
    "generic_pick_number": {
        "en": "Reply with the number of your choice.",
        "ar": "أرسل رقم اختيارك.",
        "es": "Responda con el número de su elección.",
        "fr": "Répondez avec le numéro de votre choix.",
        "ru": "Ответьте номером вашего выбора.",
        "zh": "请回复您所选项的编号。",
    },
    "generic_pick_numbers": {
        "en": "Reply with the numbers separated by commas (e.g. 1, 3).",
        "ar": "أرسل الأرقام مفصولة بفواصل (مثال: 1، 3).",
        "es": "Responda con los números separados por comas (p. ej. 1, 3).",
        "fr": "Répondez avec les numéros séparés par des virgules (ex. 1, 3).",
        "ru": "Ответьте номерами через запятую (например, 1, 3).",
        "zh": "请回复以逗号分隔的编号（例如 1、3）。",
    },
    "generic_max": {
        "en": "You can select at most {n}.",
        "ar": "يمكنك اختيار {n} كحد أقصى.",
        "es": "Puede seleccionar como máximo {n}.",
        "fr": "Vous pouvez sélectionner au maximum {n}.",
        "ru": "Можно выбрать не более {n}.",
        "zh": "最多可选择 {n} 项。",
    },
    "generic_invalid": {
        "en": "Sorry, I didn't catch that. Please try again.",
        "ar": "عذراً، لم أفهم ذلك. حاول مرة أخرى.",
        "es": "Lo siento, no entendí. Inténtelo de nuevo.",
        "fr": "Désolé, je n'ai pas compris. Veuillez réessayer.",
        "ru": "Извините, не понял. Попробуйте ещё раз.",
        "zh": "抱歉，我没听懂——请重试。",
    },
    "generic_skip": {
        "en": "Skip",
        "ar": "تخطي",
        "es": "Omitir",
        "fr": "Passer",
        "ru": "Пропустить",
        "zh": "跳过",
    },
    "skip_optional": {
        "en": "This step is optional.",
        "ar": "هذه الخطوة اختيارية.",
        "es": "Este paso es opcional.",
        "fr": "Cette étape est facultative.",
        "ru": "Этот шаг необязателен.",
        "zh": "此步骤为可选项。",
    },
    "ask_language": {
        # No language chosen yet, so this prompt is multilingual.
        "en": "Language / اللغة / Idioma / Langue / Язык / 语言",
    },
    "language_button": {
        "en": "Language",
    },
    "ask_crisis": {
        "en": "Which crisis is this report about?",
        "ar": "ما الأزمة المتعلقة بهذا البلاغ؟",
        "es": "¿Sobre qué crisis trata este informe?",
        "fr": "De quelle crise s'agit-il ?",
        "ru": "К какому кризису относится это сообщение?",
        "zh": "此报告涉及哪场危机？",
    },
    "crisis_button": {
        "en": "Pick crisis",
        "ar": "اختر",
        "es": "Elegir",
        "fr": "Choisir",
        "ru": "Выбрать",
        "zh": "选择",
    },
    "ask_photo": {
        "en": "Please send a photo of the damaged building.",
        "ar": "أرسل صورة للمبنى المتضرر من فضلك.",
        "es": "Por favor envíe una foto del edificio dañado.",
        "fr": "Veuillez envoyer une photo du bâtiment endommagé.",
        "ru": "Пожалуйста, отправьте фото поврежденного здания.",
        "zh": "请发送受损建筑的照片。",
    },
    "ask_photo_or_describe": {
        "en": "Please send a photo of the damaged building. No photo? Send a voice note 🎙️, type a description, or tap below.",
        "ar": "أرسل صورة للمبنى المتضرر من فضلك. لا توجد صورة؟ أرسل مقطعاً صوتياً 🎙️، أو اكتب وصفاً، أو اضغط أدناه.",
        "es": "Por favor envíe una foto del edificio dañado. ¿Sin foto? Envíe una nota de voz 🎙️, escriba una descripción o toque abajo.",
        "fr": "Veuillez envoyer une photo du bâtiment endommagé. Pas de photo ? Envoyez un message vocal 🎙️, tapez une description ou appuyez ci-dessous.",
        "ru": "Пожалуйста, отправьте фото поврежденного здания. Нет фото? Отправьте голосовое сообщение 🎙️, введите описание или нажмите ниже.",
        "zh": "请发送受损建筑的照片。没有照片？发送语音消息 🎙️、输入描述或点击下方。",
    },
    "btn_no_photo": {
        "en": "No photo, describe",
        "ar": "لا صورة، وصف",
        "es": "Sin foto, describir",
        "fr": "Sans photo, décrire",
        "ru": "Без фото, описать",
        "zh": "无照片 — 描述",
    },
    "photo_invalid": {
        "en": "That doesn't look like an image. Please send a photo.",
        "ar": "هذا ليس صورة. أرسل صورة من فضلك.",
        "es": "Eso no parece una imagen. Por favor envíe una foto.",
        "fr": "Ce n'est pas une image. Veuillez envoyer une photo.",
        "ru": "Это не похоже на изображение. Пришлите фото, пожалуйста.",
        "zh": "这似乎不是图片——请发送照片。",
    },
    "ask_damage": {
        "en": "How severe is the damage?",
        "ar": "ما درجة الضرر؟",
        "es": "¿Qué tan grave es el daño?",
        "fr": "Quelle est la gravité des dégâts ?",
        "ru": "Насколько серьёзен ущерб?",
        "zh": "损坏程度如何？",
    },
    "damage_minimal": {
        "en": "Minimal",
        "ar": "بسيط",
        "es": "Mínimo",
        "fr": "Minime",
        "ru": "Минимальный",
        "zh": "轻微",
    },
    "damage_partial": {
        "en": "Partial",
        "ar": "جزئي",
        "es": "Parcial",
        "fr": "Partiel",
        "ru": "Частичный",
        "zh": "部分",
    },
    "damage_complete": {
        "en": "Complete",
        "ar": "كلي",
        "es": "Total",
        "fr": "Total",
        "ru": "Полный",
        "zh": "完全",
    },
    "ask_description": {
        "en": "Add a short description?",
        "ar": "هل تريد إضافة وصف قصير؟",
        "es": "¿Agregar una descripción breve?",
        "fr": "Ajouter une brève description ?",
        "ru": "Добавить краткое описание?",
        "zh": "是否添加简短描述？",
    },
    "btn_add": {
        "en": "Add description",
        "ar": "أضف وصفاً",
        "es": "Agregar descripción",
        "fr": "Ajouter une description",
        "ru": "Добавить описание",
        "zh": "添加描述",
    },
    "btn_skip": {
        "en": "Skip",
        "ar": "تخطي",
        "es": "Omitir",
        "fr": "Passer",
        "ru": "Пропустить",
        "zh": "跳过",
    },
    "prompt_description_text": {
        "en": "Type a short description (max 1024 chars), or send a voice note 🎙️.",
        "ar": "اكتب وصفاً قصيراً (حتى 1024 حرفاً)، أو أرسل مقطعاً صوتياً 🎙️.",
        "es": "Escriba una descripción breve (máx. 1024 caracteres), o envíe una nota de voz 🎙️.",
        "fr": "Tapez une brève description (max 1024 caractères), ou envoyez un message vocal 🎙️.",
        "ru": "Введите краткое описание (до 1024 символов) или отправьте голосовое сообщение 🎙️.",
        "zh": "输入简短描述（最多 1024 个字符），或发送语音消息 🎙️。",
    },
    "prompt_describe_substitute": {
        "en": "No problem. Type a description of the building and the damage (e.g. 3-storey school, front wall collapsed), or send a voice note 🎙️.",
        "ar": "لا بأس. اكتب وصفاً للمبنى والضرر (مثال: مدرسة من ثلاثة طوابق، انهار الجدار الأمامي)، أو أرسل مقطعاً صوتياً 🎙️.",
        "es": "No hay problema. Escriba una descripción del edificio y el daño (p. ej. escuela de 3 pisos, muro frontal derrumbado), o envíe una nota de voz 🎙️.",
        "fr": "Pas de souci. Décrivez le bâtiment et les dégâts en texte (ex. école de 3 étages, mur avant effondré), ou envoyez un message vocal 🎙️.",
        "ru": "Не страшно. Опишите здание и повреждения (например: трёхэтажная школа, обрушилась передняя стена) или отправьте голосовое сообщение 🎙️.",
        "zh": "没关系——请用文字描述建筑和损坏情况（例如：三层学校，前墙倒塌），或发送语音消息 🎙️。",
    },
    "ask_debris": {
        "en": "Is there debris blocking access?",
        "ar": "هل هناك حطام يعيق الوصول؟",
        "es": "¿Hay escombros bloqueando el acceso?",
        "fr": "Y a-t-il des débris bloquant l'accès ?",
        "ru": "Есть ли обломки, перекрывающие проход?",
        "zh": "是否有碎片阻挡通道？",
    },
    "debris_yes": {
        "en": "Yes",
        "ar": "نعم",
        "es": "Sí",
        "fr": "Oui",
        "ru": "Да",
        "zh": "是",
    },
    "debris_no": {
        "en": "No",
        "ar": "لا",
        "es": "No",
        "fr": "Non",
        "ru": "Нет",
        "zh": "否",
    },
    "debris_unknown": {
        "en": "Unknown",
        "ar": "لا أعرف",
        "es": "Desconocido",
        "fr": "Inconnu",
        "ru": "Неизвестно",
        "zh": "未知",
    },
    "ask_infra": {
        "en": "What kind of infrastructure was damaged?",
        "ar": "ما نوع البنية التحتية المتضررة؟",
        "es": "¿Qué tipo de infraestructura resultó dañada?",
        "fr": "Quel type d'infrastructure a été endommagé ?",
        "ru": "Какая инфраструктура повреждена?",
        "zh": "哪类基础设施受损？",
    },
    "infra_button": {
        "en": "Pick type",
        "ar": "اختر",
        "es": "Elegir tipo",
        "fr": "Choisir",
        "ru": "Выбрать",
        "zh": "选择类型",
    },
    "multi_done": {
        "en": "Done ✓",
        "ar": "تم ✓",
        "es": "Listo ✓",
        "fr": "Terminé ✓",
        "ru": "Готово ✓",
        "zh": "完成 ✓",
    },
    "multi_selected": {
        "en": "Selected",
        "ar": "المختار",
        "es": "Seleccionados",
        "fr": "Sélectionnés",
        "ru": "Выбрано",
        "zh": "已选",
    },
    "multi_none_yet": {
        "en": "(none yet)",
        "ar": "(لا شيء بعد)",
        "es": "(ninguno aún)",
        "fr": "(aucun pour l'instant)",
        "ru": "(пока ничего)",
        "zh": "（暂无）",
    },
    "infra_residential": {
        "en": "Residential",
        "ar": "سكني",
        "es": "Residencial",
        "fr": "Résidentiel",
        "ru": "Жилой",
        "zh": "住宅",
    },
    "infra_commercial": {
        "en": "Commercial",
        "ar": "تجاري",
        "es": "Comercial",
        "fr": "Commercial",
        "ru": "Коммерческий",
        "zh": "商业",
    },
    "infra_government": {
        "en": "Government",
        "ar": "حكومي",
        "es": "Gubernamental",
        "fr": "Gouvernemental",
        "ru": "Государственный",
        "zh": "政府",
    },
    "infra_utility": {
        "en": "Utility",
        "ar": "خدمات",
        "es": "Servicios",
        "fr": "Services publics",
        "ru": "Коммунальный",
        "zh": "公用设施",
    },
    "infra_transport": {
        "en": "Transport",
        "ar": "نقل",
        "es": "Transporte",
        "fr": "Transport",
        "ru": "Транспорт",
        "zh": "交通",
    },
    "infra_community": {
        "en": "Community",
        "ar": "مجتمعي",
        "es": "Comunitario",
        "fr": "Communautaire",
        "ru": "Общественный",
        "zh": "社区",
    },
    "infra_public_spaces": {
        "en": "Public spaces",
        "ar": "أماكن عامة",
        "es": "Espacios públicos",
        "fr": "Espaces publics",
        "ru": "Общественные места",
        "zh": "公共场所",
    },
    "infra_other": {
        "en": "Other",
        "ar": "أخرى",
        "es": "Otro",
        "fr": "Autre",
        "ru": "Другое",
        "zh": "其他",
    },
    "prompt_infra_other": {
        "en": "Describe the other infrastructure type.",
        "ar": "صف نوع البنية الأخرى.",
        "es": "Describa el otro tipo de infraestructura.",
        "fr": "Décrivez l'autre type d'infrastructure.",
        "ru": "Опишите другой тип инфраструктуры.",
        "zh": "请描述其他基础设施类型。",
    },
    "multi_at_least_one": {
        "en": "Pick at least one type before Done.",
        "ar": "اختر نوعاً واحداً على الأقل قبل تم.",
        "es": "Elija al menos un tipo antes de Listo.",
        "fr": "Choisissez au moins un type avant Terminé.",
        "ru": "Выберите хотя бы один тип перед нажатием Готово.",
        "zh": "完成前请至少选择一项。",
    },
    "ask_nature_pick": {
        "en": "Pick the closest match.",
        "ar": "اختر الأقرب.",
        "es": "Elija la opción más cercana.",
        "fr": "Choisissez l'option la plus proche.",
        "ru": "Выберите наиболее подходящее.",
        "zh": "请选择最接近的选项。",
    },
    "nature_other": {
        "en": "Other",
        "ar": "أخرى",
        "es": "Otro",
        "fr": "Autre",
        "ru": "Другое",
        "zh": "其他",
    },
    "prompt_nature_other": {
        "en": "Describe the crisis nature.",
        "ar": "صف طبيعة الأزمة.",
        "es": "Describa la naturaleza de la crisis.",
        "fr": "Décrivez la nature de la crise.",
        "ru": "Опишите характер кризиса.",
        "zh": "请描述危机性质。",
    },
    "ask_location": {
        "en": "Please share the location pin of the damaged building.",
        "ar": "شارك موقع المبنى المتضرر من فضلك.",
        "es": "Por favor comparta la ubicación del edificio dañado.",
        "fr": "Veuillez partager la position du bâtiment endommagé.",
        "ru": "Пожалуйста, отправьте геометку поврежденного здания.",
        "zh": "请分享受损建筑的位置。",
    },
    "location_fallback_hint": {
        "en": "Tap 📎 → Location → Send your current location.",
        "ar": "اضغط 📎 ← الموقع ← أرسل موقعك الحالي.",
        "es": "Toque 📎 → Ubicación → Enviar mi ubicación actual.",
        "fr": "Appuyez 📎 → Position → Envoyer ma position actuelle.",
        "ru": "Нажмите 📎 → Местоположение → Отправить текущее местоположение.",
        "zh": "点击 📎 → 位置 → 发送当前位置。",
    },
    "cant_share_location": {
        "en": "Can't share your location? Send a voice note 🎙️, type directions, or tap below.",
        "ar": "لا يمكنك مشاركة موقعك؟ أرسل مقطعاً صوتياً 🎙️، أو اكتب الاتجاهات، أو اضغط أدناه.",
        "es": "¿No puede compartir su ubicación? Envíe una nota de voz 🎙️, escriba indicaciones o toque abajo.",
        "fr": "Impossible de partager votre position ? Envoyez un message vocal 🎙️, saisissez un itinéraire ou appuyez ci-dessous.",
        "ru": "Не можете поделиться местоположением? Отправьте голосовое сообщение 🎙️, введите маршрут или нажмите ниже.",
        "zh": "无法分享位置？发送语音消息 🎙️、输入路线说明或点击下方。",
    },
    "btn_type_directions": {
        "en": "Type directions",
        "ar": "اكتب الاتجاهات",
        "es": "Indicaciones",
        "fr": "Itinéraire",
        "ru": "Ввести описание",
        "zh": "输入路线",
    },
    "ask_route": {
        "en": "Describe how to reach the building: street, landmarks, or neighbourhood. You can also send a voice note 🎙️.",
        "ar": "صف كيفية الوصول إلى المبنى: الشارع أو المعالم أو الحي. يمكنك أيضاً إرسال مقطع صوتي 🎙️.",
        "es": "Describa cómo llegar al edificio: calle, puntos de referencia o barrio. También puede enviar una nota de voz 🎙️.",
        "fr": "Décrivez comment atteindre le bâtiment : rue, points de repère ou quartier. Vous pouvez aussi envoyer un message vocal 🎙️.",
        "ru": "Опишите, как добраться до здания: улица, ориентиры или район. Можно также отправить голосовое сообщение 🎙️.",
        "zh": "请描述如何到达该建筑——街道、地标或社区。也可以发送语音消息 🎙️。",
    },
    "review_route": {
        "en": "Directions",
        "ar": "الاتجاهات",
        "es": "Indicaciones",
        "fr": "Itinéraire",
        "ru": "Описание пути",
        "zh": "路线",
    },
    "review_title": {
        "en": "Review your report",
        "ar": "راجع البلاغ",
        "es": "Revise su informe",
        "fr": "Vérifiez votre rapport",
        "ru": "Проверьте сообщение",
        "zh": "查看您的报告",
    },
    "review_crisis": {
        "en": "Crisis",
        "ar": "الأزمة",
        "es": "Crisis",
        "fr": "Crise",
        "ru": "Кризис",
        "zh": "危机",
    },
    "review_damage": {
        "en": "Damage",
        "ar": "الضرر",
        "es": "Daño",
        "fr": "Dégâts",
        "ru": "Ущерб",
        "zh": "损害",
    },
    "review_description": {
        "en": "Description",
        "ar": "الوصف",
        "es": "Descripción",
        "fr": "Description",
        "ru": "Описание",
        "zh": "描述",
    },
    "review_debris": {
        "en": "Debris",
        "ar": "حطام",
        "es": "Escombros",
        "fr": "Débris",
        "ru": "Обломки",
        "zh": "碎片",
    },
    "review_infra": {
        "en": "Type",
        "ar": "النوع",
        "es": "Tipo",
        "fr": "Type",
        "ru": "Тип",
        "zh": "类型",
    },
    "review_nature": {
        "en": "Nature",
        "ar": "الطبيعة",
        "es": "Naturaleza",
        "fr": "Nature",
        "ru": "Характер",
        "zh": "性质",
    },
    "review_location": {
        "en": "Location",
        "ar": "الموقع",
        "es": "Ubicación",
        "fr": "Emplacement",
        "ru": "Местоположение",
        "zh": "位置",
    },
    "review_question": {
        "en": "Submit?",
        "ar": "إرسال؟",
        "es": "¿Enviar?",
        "fr": "Envoyer ?",
        "ru": "Отправить?",
        "zh": "是否提交？",
    },
    "btn_submit": {
        "en": "Submit",
        "ar": "إرسال",
        "es": "Enviar",
        "fr": "Envoyer",
        "ru": "Отправить",
        "zh": "提交",
    },
    "btn_edit": {
        "en": "Edit",
        "ar": "تعديل",
        "es": "Editar",
        "fr": "Modifier",
        "ru": "Изменить",
        "zh": "编辑",
    },
    "btn_cancel": {
        "en": "Cancel",
        "ar": "إلغاء",
        "es": "Cancelar",
        "fr": "Annuler",
        "ru": "Отмена",
        "zh": "取消",
    },
    "cancelled": {
        "en": "Cancelled. Text anything to start again.",
        "ar": "تم الإلغاء. أرسل أي رسالة للبدء من جديد.",
        "es": "Cancelado. Envíe cualquier mensaje para empezar de nuevo.",
        "fr": "Annulé. Envoyez n'importe quel message pour recommencer.",
        "ru": "Отменено. Отправьте любое сообщение, чтобы начать заново.",
        "zh": "已取消——发送任何消息以重新开始。",
    },
    "submit_failed": {
        "en": "Couldn't submit right now. Please try again in a moment.",
        "ar": "تعذّر الإرسال الآن. حاول مرة أخرى بعد قليل.",
        "es": "No se pudo enviar ahora. Inténtelo de nuevo en un momento.",
        "fr": "Impossible d'envoyer maintenant. Réessayez dans un instant.",
        "ru": "Не удалось отправить. Попробуйте через мгновение.",
        "zh": "暂时无法提交——请稍后再试。",
    },
}


def _strings(lang: Lang) -> dict[str, str]:
    return resolve_strings(_STRINGS, lang)


def _lang(session: Session) -> Lang:
    return normalize_lang(session.language)


class TemplateConversationFlow:
    def __init__(
        self,
        *,
        provider: Provider,
        sessions: SessionStore,
        crises: CrisisService,
        submitter: ReportSubmitter,
        lang_prefs: LanguagePrefs,
        privacy_policy_url: str = "",
    ) -> None:
        self._provider = provider
        self._sessions = sessions
        self._crises = crises
        self._submitter = submitter
        self._lang_prefs = lang_prefs
        self._privacy_policy_url = privacy_policy_url

    async def handle(self, msg: InboundMessage) -> None:
        _logger.info(
            "whatsapp.template.handle from=%s body=%r has_media=%s has_pin=%s list=%s btn=%s",
            msg.from_e164,
            msg.body,
            bool(msg.num_media),
            msg.latitude is not None,
            msg.list_id,
            msg.button_payload,
        )
        await self._sessions.gc()
        session = await self._sessions.get(msg.from_e164)

        restart_signal = (
            parse_reset(msg.body) or msg.list_id == "restart" or msg.button_payload == "restart"
        )
        if session is None or restart_signal:
            if session is not None:
                await self._sessions.delete(session.phone_e164)
            session = Session(phone_e164=msg.from_e164)
            session.scratch["origin"] = "template"
            await self._bootstrap(session)
            remembered = self._lang_prefs.get(msg.from_e164)
            if remembered is not None:
                session.language = remembered
                await self._send_step(session, "CRISIS", intro=True)
            else:
                await self._send_step(session, "LANGUAGE")
            await self._sessions.upsert(session)
            return

        step = cast(Step, session.scratch.get("template_step") or "LANGUAGE")
        if step != "PHOTO" and _is_stray_media(msg):
            _logger.info(
                "whatsapp.template.stray_media_ignored from=%s step=%s",
                msg.from_e164,
                step,
            )
            return
        await self._step(session, step, msg)

        if session.scratch.get("template_step") == "DONE":
            await self._sessions.delete(session.phone_e164)
        else:
            await self._sessions.upsert(session)

    async def _bootstrap(self, session: Session) -> None:
        crises = await self._crises.list_active()
        ordered = list(crises)
        session.active_crisis_choices = {c.name: str(c.id) for c in ordered}
        session.scratch["template_crisis_by_id"] = {str(c.id): c.name for c in ordered}
        session.state = "active"

    async def _load_form(self, session: Session) -> None:
        """On failure the plan falls back to the default schema."""
        if session.crisis_id is None:
            return
        try:
            form = await self._crises.get_form(session.crisis_id)
        except Exception:
            _logger.exception("whatsapp.template.load_form_failed")
            return
        if form is not None:
            session.form_schema, session.form_version = form

    def _plan(self, session: Session) -> list[Slot]:
        return plan_from_schema(session.form_schema, _lang(session))

    def _description_enabled(self, session: Session) -> bool:
        """Only an enabled description page can replace the photo."""
        return any(
            not slot.is_generic and slot.kind == "description" for slot in self._plan(session)
        )

    def _current_generic(self, session: Session) -> Slot | None:
        slot = self._current_slot(session)
        return slot if slot is not None and slot.is_generic else None

    def _current_slot(self, session: Session) -> Slot | None:
        plan = self._plan(session)
        idx = session.scratch.get("template_slot_idx")
        if isinstance(idx, int) and 0 <= idx < len(plan):
            return plan[idx]
        return None

    def _current_is_optional_builtin(self, session: Session) -> bool:
        slot = self._current_slot(session)
        return slot is not None and not slot.is_generic and not slot.required

    async def _dispatch_slot(self, session: Session, idx: int) -> None:
        """Send the step for slot ``idx``; past the end go to REVIEW."""
        plan = self._plan(session)
        if idx >= len(plan):
            await self._send_step(session, "REVIEW")
            return
        session.scratch["template_slot_idx"] = idx
        slot = plan[idx]
        if slot.is_generic:
            await self._send_step(session, "GENERIC")
            return
        await self._send_step(session, _BUILTIN_STEP_BY_KIND[slot.kind])

    async def _resume_review_if_editing(self, session: Session) -> bool:
        """Return to REVIEW if the user is editing a single answer from it."""
        if session.scratch.pop("template_editing", None):
            await self._send_step(session, "REVIEW")
            return True
        return False

    async def _advance(self, session: Session) -> None:
        if await self._resume_review_if_editing(session):
            return
        idx = session.scratch.get("template_slot_idx")
        current = idx if isinstance(idx, int) else -1
        await self._dispatch_slot(session, current + 1)

    async def _step(self, session: Session, step: Step, msg: InboundMessage) -> None:
        handlers = {
            "LANGUAGE": self._on_language,
            "CRISIS": self._on_crisis,
            "PHOTO": self._on_photo,
            "DAMAGE": self._on_damage,
            "DESCRIPTION": self._on_description,
            "DESCRIPTION_TEXT": self._on_description_text,
            "DEBRIS": self._on_debris,
            "INFRA_TYPE": self._on_infra_type,
            "INFRA_TYPE_OTHER": self._on_infra_type_other,
            "CRISIS_NATURE": self._on_nature_pick,
            "CRISIS_NATURE_OTHER": self._on_nature_other,
            "GENERIC": self._on_generic,
            "LOCATION": self._on_location,
            "ROUTE": self._on_route,
            "REVIEW": self._on_review,
            "EDIT_MENU": self._on_edit_menu,
        }
        fn = handlers.get(step)
        if fn is None:
            _logger.warning("whatsapp.template.unknown_step step=%s", step)
            await self._send_step(session, "CRISIS")
            return
        await fn(session, msg)

    async def _on_language(self, session: Session, msg: InboundMessage) -> None:
        value = _parse_list(msg, prefix="lang:")
        if value is None or value not in SUPPORTED_LANGS:
            await self._send_step(session, "LANGUAGE")
            return
        session.language = value
        self._lang_prefs.set(session.phone_e164, value)
        await self._send_step(session, "CRISIS", intro=True)

    async def _on_crisis(self, session: Session, msg: InboundMessage) -> None:
        cid = _parse_list(msg, prefix="crisis:")
        if cid is None:
            # Numbered-text fallback when there are too many crises for a list.
            choices = list(session.active_crisis_choices.values())
            idx = _parse_one_index(_body(msg), len(choices))
            if idx is not None:
                cid = choices[idx]
        if cid is None:
            await self._send_step(session, "CRISIS")
            return
        by_id = cast(dict[str, str], session.scratch.get("template_crisis_by_id") or {})
        name = by_id.get(cid)
        if name is None:
            await self._send_step(session, "CRISIS")
            return
        try:
            session.crisis_id = uuid.UUID(cid)
        except ValueError:
            await self._send_step(session, "CRISIS")
            return
        session.crisis_name = name
        await self._load_form(session)
        await self._send_step(session, "PHOTO")

    async def _on_photo(self, session: Session, msg: InboundMessage) -> None:
        s = _strings(_lang(session))
        if msg.num_media:
            if capture_photo(session, msg) == "invalid":
                await self._provider.send_text(session.phone_e164, s["photo_invalid"])
                return
            if await self._resume_review_if_editing(session):
                return
            await self._send_step(session, "DAMAGE")
            return
        if self._description_enabled(session):
            # Check the button before `body` so its label is never stored as the description.
            if _parse_button(msg, prefix="photo:") == "skip":
                if await self._resume_review_if_editing(session):
                    return
                await self._send_step(session, "DESCRIPTION_TEXT")
                return
            body = _body(msg)
            if body:
                session.infra_description = body[:MAX_DESCRIPTION_CHARS]
                if await self._resume_review_if_editing(session):
                    return
                await self._send_step(session, "DAMAGE")
                return
        await self._send_step(session, "PHOTO")

    async def _on_damage(self, session: Session, msg: InboundMessage) -> None:
        value = _parse_button(msg, prefix="damage:")
        if not is_damage_class(value):
            await self._send_step(session, "DAMAGE")
            return
        session.damage_class = value
        if await self._resume_review_if_editing(session):
            return
        await self._dispatch_slot(session, 0)

    async def _on_description(self, session: Session, msg: InboundMessage) -> None:
        choice = _parse_button(msg, prefix="desc:")
        if choice == "add":
            await self._send_step(session, "DESCRIPTION_TEXT")
            return
        if choice == "skip":
            await self._advance(session)
            return
        # Typed text or a transcribed voice note counts without tapping Add.
        body = _body(msg)
        if body:
            session.infra_description = body[:MAX_DESCRIPTION_CHARS]
            await self._advance(session)
            return
        await self._send_step(session, "DESCRIPTION")

    async def _on_description_text(self, session: Session, msg: InboundMessage) -> None:
        body = _body(msg)
        if not body:
            await self._send_step(session, "DESCRIPTION_TEXT")
            return
        session.infra_description = body[:MAX_DESCRIPTION_CHARS]
        if session.damage_class is None:
            # Photo substitute path: damage class still needed.
            await self._send_step(session, "DAMAGE")
            return
        await self._advance(session)

    async def _on_debris(self, session: Session, msg: InboundMessage) -> None:
        if self._current_is_optional_builtin(session) and _is_skip(msg):
            await self._advance(session)
            return
        value = _parse_button(msg, prefix="debris:")
        if not is_debris(value):
            await self._send_step(session, "DEBRIS")
            return
        session.debris = value
        await self._advance(session)

    async def _on_infra_type(self, session: Session, msg: InboundMessage) -> None:
        # Single-select over WhatsApp; the field still stores a one-element list.
        optional = self._current_is_optional_builtin(session)
        token = _parse_list(msg, prefix="infra:")
        if optional and (token == "skip" or _is_skip(msg)):
            await self._advance(session)
            return
        if token is None or token not in INFRA_TYPES:
            await self._send_step(session, "INFRA_TYPE")
            return
        session.infra_type = [token]
        if token == "other":
            await self._send_step(session, "INFRA_TYPE_OTHER")
            return
        await self._advance(session)

    async def _on_infra_type_other(self, session: Session, msg: InboundMessage) -> None:
        # Optional; a typed 0 skips it.
        if not _is_skip(msg):
            body = _body(msg)
            if body:
                session.infra_type_other = body[:MAX_OTHER_TEXT_CHARS]
        await self._advance(session)

    async def _on_nature_pick(self, session: Session, msg: InboundMessage) -> None:
        if self._current_is_optional_builtin(session) and _is_skip(msg):
            await self._advance(session)
            return
        n = len(NATURE_LABELS)
        tok = _parse_list(msg, prefix="nature:")
        if tok == "other" or _body(msg) == str(n + 1):
            session.crisis_nature = NATURE_OTHER
            await self._send_step(session, "CRISIS_NATURE_OTHER")
            return
        idx: int | None = None
        if tok is not None and tok.isdigit() and 0 <= int(tok) < n:
            idx = int(tok)
        else:
            idx = _parse_one_index(_body(msg), n)
        if idx is None:
            await self._send_step(session, "CRISIS_NATURE")
            return
        session.crisis_nature = NATURE_LABELS[idx]
        await self._advance(session)

    async def _on_nature_other(self, session: Session, msg: InboundMessage) -> None:
        # Optional; a typed 0 skips it.
        if not _is_skip(msg):
            body = _body(msg)
            if body:
                session.crisis_nature_other = body[:MAX_OTHER_TEXT_CHARS]
        await self._advance(session)

    async def _on_generic(self, session: Session, msg: InboundMessage) -> None:
        slot = self._current_generic(session)
        if slot is None:
            # Slot pointer lost, e.g. the schema changed mid-flow.
            await self._advance(session)
            return
        s = _strings(_lang(session))
        label = slot.target

        if not slot.required and _is_skip(msg):
            await self._advance(session)
            return

        if slot.qtype == "free_text":
            body = _body(msg)
            if not body:
                await self._send_step(session, "GENERIC")
                return
            session.generic_answers[label] = body[:MAX_DESCRIPTION_CHARS]
            await self._advance(session)
            return

        if slot.qtype == "single_select":
            chosen = _read_single_generic(msg, slot)
            if chosen is None:
                await self._provider.send_text(session.phone_e164, s["generic_invalid"])
                await self._send_step(session, "GENERIC")
                return
            session.generic_answers[label] = chosen
            await self._advance(session)
            return

        if _generic_fits_list(slot):
            await self._handle_multi_list(session, msg, slot, s)
        else:
            await self._handle_multi_numbered(session, msg, slot, s)

    async def _handle_multi_list(
        self, session: Session, msg: InboundMessage, slot: Slot, s: dict[str, str]
    ) -> None:
        tok = _parse_list(msg, prefix="gen:")
        current = _as_str_list(session.generic_answers.get(slot.target))
        if tok == "done":
            if slot.required and not current:
                await self._provider.send_text(session.phone_e164, s["multi_at_least_one"])
                await self._send_step(session, "GENERIC")
                return
            session.generic_answers[slot.target] = current
            await self._advance(session)
            return
        if tok is None or not tok.isdigit() or not (0 <= int(tok) < len(slot.options)):
            await self._send_step(session, "GENERIC")
            return
        option = slot.options[int(tok)]
        if option in current:
            current.remove(option)
        elif slot.max_select is not None and len(current) >= slot.max_select:
            await self._provider.send_text(
                session.phone_e164, s["generic_max"].format(n=slot.max_select)
            )
        else:
            current.append(option)
        session.generic_answers[slot.target] = current
        await self._send_step(session, "GENERIC")

    async def _handle_multi_numbered(
        self, session: Session, msg: InboundMessage, slot: Slot, s: dict[str, str]
    ) -> None:
        picks = _parse_index_list(_body(msg), len(slot.options))
        if picks is None:
            await self._provider.send_text(session.phone_e164, s["generic_invalid"])
            await self._send_step(session, "GENERIC")
            return
        if slot.required and not picks:
            await self._provider.send_text(session.phone_e164, s["multi_at_least_one"])
            await self._send_step(session, "GENERIC")
            return
        if slot.max_select is not None and len(picks) > slot.max_select:
            await self._provider.send_text(
                session.phone_e164, s["generic_max"].format(n=slot.max_select)
            )
            await self._send_step(session, "GENERIC")
            return
        session.generic_answers[slot.target] = [slot.options[i] for i in picks]
        await self._advance(session)

    async def _on_location(self, session: Session, msg: InboundMessage) -> None:
        if msg.latitude is not None and msg.longitude is not None:
            session.location = (msg.latitude, msg.longitude)
            # A pin wins on the review screen; any earlier typed directions are still submitted.
            await self._advance(session)
            return
        # Check the button before `body` so its label is never stored as the route.
        if _parse_button(msg, prefix="route:") == "type":
            await self._send_step(session, "ROUTE")
            return
        # Typed or transcribed directions satisfy the location-OR-route gate.
        body = _body(msg)
        if body:
            session.route_description = body[:MAX_ROUTE_CHARS]
            await self._advance(session)
            return
        await self._send_step(session, "LOCATION")

    async def _on_route(self, session: Session, msg: InboundMessage) -> None:
        body = _body(msg)
        if not body:
            await self._send_step(session, "ROUTE")
            return
        session.route_description = body[:MAX_ROUTE_CHARS]
        await self._advance(session)

    async def _on_review(self, session: Session, msg: InboundMessage) -> None:
        s = _strings(_lang(session))
        choice = _parse_button(msg, prefix="review:")
        if choice == "submit":
            if not draft_complete(session):
                _logger.warning(
                    "whatsapp.template.submit_rejected_incomplete from=%s",
                    session.phone_e164,
                )
                await self._send_step(session, "REVIEW")
                return
            ok = await self._do_submit(session)
            if ok:
                session.scratch["template_step"] = "DONE"
            return
        if choice == "edit":
            await self._send_step(session, "EDIT_MENU")
            return
        if choice == "cancel":
            await self._provider.send_text(session.phone_e164, s["cancelled"])
            session.scratch["template_step"] = "DONE"
            return
        await self._send_step(session, "REVIEW")

    def _edit_targets(self, session: Session) -> list[tuple[str, str]]:
        """(edit-token, label) pairs in walk order: photo, damage, then each schema slot."""
        s = _strings(_lang(session))
        targets: list[tuple[str, str]] = [
            ("photo", s["review_photo"]),
            ("damage", s["review_damage"]),
        ]
        builtin_labels = {
            "location": s["review_location"],
            "description": s["review_description"],
            "debris": s["review_debris"],
            "infra_type": s["review_infra"],
            "crisis_nature": s["review_nature"],
        }
        for idx, slot in enumerate(self._plan(session)):
            if slot.is_generic:
                targets.append((f"generic:{idx}", slot.target[:LIST_ROW_TITLE_MAX]))
            else:
                targets.append((slot.kind, builtin_labels.get(slot.kind, slot.kind)))
        return targets

    async def _on_edit_menu(self, session: Session, msg: InboundMessage) -> None:
        token = _parse_list(msg, prefix="edit:")
        if token is None:
            await self._send_step(session, "EDIT_MENU")
            return
        valid = {t for t, _ in self._edit_targets(session)}
        if token not in valid:
            await self._send_step(session, "EDIT_MENU")
            return
        # From here on, completing the chosen step returns to REVIEW.
        session.scratch["template_editing"] = True
        if token.startswith("generic:"):
            session.scratch["template_slot_idx"] = int(token.split(":", 1)[1])
            await self._send_step(session, "GENERIC")
            return
        await self._send_step(session, _EDIT_STEP_BY_TOKEN[token])

    async def _send_step(self, session: Session, step: Step, *, intro: bool = False) -> None:
        session.scratch["template_step"] = step
        s = _strings(_lang(session))
        to = session.phone_e164

        if step == "LANGUAGE":
            rows = [
                ListRow(id=f"lang:{code}", title=LANGUAGE_NATIVE_NAMES[code])
                for code in SUPPORTED_LANGS
            ]
            # No restart row: nothing to restart on first contact.
            await self._provider.send_list(to, s["ask_language"], s["language_button"], rows)
            return

        if step == "CRISIS":
            if intro:
                consent = s["consent_notice"].format(privacy_url=self._privacy_policy_url)
                await self._provider.send_text(to, s["intro"] + "\n\n" + consent)
            choices = list(session.active_crisis_choices.items())
            if not choices:
                await self._provider.send_text(to, s["ask_crisis"])
                return
            if len(choices) <= _MAX_CONTENT_ROWS:
                rows = [
                    ListRow(id=f"crisis:{cid}", title=name[:LIST_ROW_TITLE_MAX])
                    for name, cid in choices
                ]
                rows.append(_restart_row(s))
                await self._provider.send_list(to, s["ask_crisis"], s["crisis_button"], rows)
            else:
                # Too many crises for a 10-row list.
                numbered = "\n".join(f"{i + 1}. {name}" for i, (name, _) in enumerate(choices))
                body = f"{s['ask_crisis']}\n\n{numbered}\n\n{s['generic_pick_number']}"
                await self._provider.send_text(to, body)
            return

        if step == "PHOTO":
            if self._description_enabled(session):
                await self._provider.send_buttons(
                    to,
                    _hint(s, s["ask_photo_or_describe"]),
                    [Button(id="photo:skip", title=s["btn_no_photo"])],
                )
            else:
                await self._provider.send_text(to, _hint(s, s["ask_photo"]))
            return

        if step == "DAMAGE":
            await self._provider.send_buttons(
                to,
                _hint(s, s["ask_damage"]),
                [
                    Button(id="damage:minimal", title=s["damage_minimal"]),
                    Button(id="damage:partial", title=s["damage_partial"]),
                    Button(id="damage:complete", title=s["damage_complete"]),
                ],
            )
            return

        if step == "DESCRIPTION":
            editing = bool(session.scratch.get("template_editing"))
            has_desc = session.has_description
            if has_desc and not editing:
                await self._advance(session)
                return
            if not session.has_photo and not has_desc:
                # The description is the mandatory photo substitute here.
                await self._send_step(session, "DESCRIPTION_TEXT")
                return
            await self._provider.send_buttons(
                to,
                _hint(s, s["ask_description"]),
                [
                    Button(id="desc:add", title=s["btn_add"]),
                    Button(id="desc:skip", title=s["btn_skip"]),
                ],
            )
            return

        if step == "DESCRIPTION_TEXT":
            has_desc = session.has_description
            prompt = (
                s["prompt_describe_substitute"]
                if not session.has_photo and not has_desc
                else s["prompt_description_text"]
            )
            await self._provider.send_text(to, _hint(s, prompt))
            return

        if step == "DEBRIS":
            # WhatsApp allows 3 reply buttons, so Skip goes in a follow-up message.
            await self._provider.send_buttons(
                to,
                _hint(s, s["ask_debris"]),
                [
                    Button(id="debris:yes", title=s["debris_yes"]),
                    Button(id="debris:no", title=s["debris_no"]),
                    Button(id="debris:unknown", title=s["debris_unknown"]),
                ],
            )
            if self._current_is_optional_builtin(session):
                await self._send_skip_button(to, s)
            return

        if step == "INFRA_TYPE":
            rows = [ListRow(id=f"infra:{v}", title=s[f"infra_{v}"]) for v in INFRA_TYPES]
            if self._current_is_optional_builtin(session):
                rows.append(ListRow(id="infra:skip", title=s["generic_skip"]))
            rows.append(_restart_row(s))
            await self._provider.send_list(to, s["ask_infra"], s["infra_button"], rows)
            return

        if step == "INFRA_TYPE_OTHER":
            await self._provider.send_text(to, _hint(s, s["prompt_infra_other"]))
            await self._send_skip_button(to, s)
            return

        if step == "CRISIS_NATURE":
            # 10 natures + "Other" exceed a 10-row list, so this is numbered text.
            numbered = "\n".join(f"{i + 1}. {label}" for i, label in enumerate(NATURE_LABELS))
            other_line = f"{len(NATURE_LABELS) + 1}. {s['nature_other']}"
            body = (
                f"{s['ask_nature_pick']}\n\n{numbered}\n{other_line}\n\n{s['generic_pick_number']}"
            )
            await self._provider.send_text(to, _hint(s, body))
            if self._current_is_optional_builtin(session):
                await self._send_skip_button(to, s)
            return

        if step == "CRISIS_NATURE_OTHER":
            await self._provider.send_text(to, _hint(s, s["prompt_nature_other"]))
            await self._send_skip_button(to, s)
            return

        if step == "GENERIC":
            slot = self._current_generic(session)
            if slot is None:
                await self._advance(session)
                return
            await self._send_generic(session, slot, s)
            return

        if step == "LOCATION":
            try:
                await self._provider.send_location_request(to, s["ask_location"])
            except NotImplementedError:
                await self._provider.send_text(
                    to, s["ask_location"] + "\n\n" + s["location_fallback_hint"]
                )
            # A location request cannot carry buttons, so this is a second message.
            await self._provider.send_buttons(
                to,
                _hint(s, s["cant_share_location"]),
                [Button(id="route:type", title=s["btn_type_directions"])],
            )
            return

        if step == "ROUTE":
            await self._provider.send_text(to, _hint(s, s["ask_route"]))
            return

        if step == "REVIEW":
            await self._provider.send_text(to, _render_review(session, s))
            await self._provider.send_buttons(
                to,
                _hint(s, s["review_question"]),
                [
                    Button(id="review:submit", title=s["btn_submit"]),
                    Button(id="review:edit", title=s["btn_edit"]),
                    Button(id="review:cancel", title=s["btn_cancel"]),
                ],
            )
            return

        if step == "EDIT_MENU":
            targets = self._edit_targets(session)
            rows = [
                ListRow(id=f"edit:{token}", title=label[:LIST_ROW_TITLE_MAX])
                for token, label in targets[:_MAX_CONTENT_ROWS]
            ]
            rows.append(_restart_row(s))
            await self._provider.send_list(to, s["ask_edit"], s["generic_button"], rows)
            return

    async def _send_skip_button(self, to: str, s: dict[str, str]) -> None:
        """For prompts that cannot host a Skip row or button."""
        await self._provider.send_buttons(
            to, s["skip_optional"], [Button(id="gen:skip", title=s["generic_skip"])]
        )

    async def _send_generic(self, session: Session, slot: Slot, s: dict[str, str]) -> None:
        to = session.phone_e164
        label = slot.target

        optional = not slot.required

        if slot.qtype == "free_text":
            await self._provider.send_text(to, _hint(s, f"{label}\n\n{s['generic_free_hint']}"))
            if optional:
                await self._send_skip_button(to, s)
            return

        if _generic_fits_list(slot):
            rows = [ListRow(id=f"gen:{i}", title=opt) for i, opt in enumerate(slot.options)]
            body = label
            if slot.qtype == "multi_select":
                selected = _as_str_list(session.generic_answers.get(label))
                shown = ", ".join(selected) if selected else s["multi_none_yet"]
                body = f"{label}\n\n{s['multi_selected']}: {shown}"
                rows.append(ListRow(id="gen:done", title=s["multi_done"]))
            if optional:
                rows.append(ListRow(id="gen:skip", title=s["generic_skip"]))
            rows.append(_restart_row(s))
            await self._provider.send_list(to, body, s["generic_button"], rows)
            return

        numbered = "\n".join(f"{i + 1}. {opt}" for i, opt in enumerate(slot.options))
        if slot.qtype == "multi_select":
            instruction = s["generic_pick_numbers"]
            if slot.max_select is not None:
                instruction += " " + s["generic_max"].format(n=slot.max_select)
        else:
            instruction = s["generic_pick_number"]
        await self._provider.send_text(to, _hint(s, f"{label}\n\n{numbered}\n\n{instruction}"))
        if optional:
            await self._send_skip_button(to, s)

    async def _do_submit(self, session: Session) -> bool:
        s = _strings(_lang(session))
        try:
            report_id = await self._submitter.submit(session)
        except Exception:
            _logger.exception("whatsapp.template.submit_failed")
            await self._provider.send_text(session.phone_e164, s["submit_failed"])
            return False
        ref = str(report_id)[:8]
        body = strings_for(_lang(session)).submitted.format(ref=ref)
        await self._provider.send_text(session.phone_e164, body)
        session.state = "done"
        return True


def _body(msg: InboundMessage) -> str:
    return (msg.body or "").strip()


def _parse_list(msg: InboundMessage, *, prefix: str) -> str | None:
    raw = msg.list_id or ""
    if raw.startswith(prefix):
        return raw[len(prefix) :] or None
    return None


def _parse_button(msg: InboundMessage, *, prefix: str) -> str | None:
    raw = msg.button_payload or ""
    if raw.startswith(prefix):
        return raw[len(prefix) :] or None
    return None


def _read_single_generic(msg: InboundMessage, slot: Slot) -> str | None:
    """Accepts a list tap or a typed number."""
    tok = _parse_list(msg, prefix="gen:")
    if tok is not None and tok.isdigit():
        i = int(tok)
        if 0 <= i < len(slot.options):
            return slot.options[i]
    idx = _parse_one_index(_body(msg), len(slot.options))
    if idx is not None:
        return slot.options[idx]
    return None


def _generic_fits_list(slot: Slot) -> bool:
    # Meta truncates row titles, so options that don't fit fall back to numbered
    # text. "Start over" takes one row; multi-select also reserves one for "Done".
    reserved = 1 + (1 if slot.qtype == "multi_select" else 0)
    usable = LIST_MAX_ROWS - reserved
    return len(slot.options) <= usable and all(
        len(opt) <= LIST_ROW_TITLE_MAX for opt in slot.options
    )


def _is_skip(msg: InboundMessage) -> bool:
    """The ``gen:skip`` list row or button, or a typed ``0`` as a silent fallback."""
    return msg.list_id == "gen:skip" or msg.button_payload == "gen:skip" or _body(msg) == "0"


def _is_stray_media(msg: InboundMessage) -> bool:
    """A media-only message with no caption, button, list pick, or pin.

    A multi-photo send arrives as one webhook per image; the extras land on
    later steps and are dropped instead of re-prompting once per image.
    """
    return (
        bool(msg.num_media)
        and not _body(msg)
        and msg.button_payload is None
        and msg.list_id is None
        and msg.latitude is None
    )


def _as_str_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [v for v in cast(list[object], value) if isinstance(v, str)]
    return []


def _parse_one_index(body: str, count: int) -> int | None:
    if not body.isdigit():
        return None
    i = int(body) - 1
    return i if 0 <= i < count else None


def _parse_index_list(body: str, count: int) -> list[int] | None:
    """Unique 0-based indices from 1-based numbers; None if any token is invalid."""
    tokens = [t for t in body.replace(",", " ").split() if t]
    if not tokens:
        return []
    out: list[int] = []
    for tok in tokens:
        if not tok.isdigit():
            return None
        i = int(tok) - 1
        if not (0 <= i < count):
            return None
        if i not in out:
            out.append(i)
    return out


def _render_review(session: Session, s: dict[str, str]) -> str:
    plan = plan_from_schema(session.form_schema, _lang(session))
    enabled_kinds = {slot.kind for slot in plan if not slot.is_generic}

    lines = [f"*{s['review_title']}*"]
    lines.append(f"{s['review_crisis']}: {session.crisis_name or '—'}")
    lines.append(f"{s['review_damage']}: {session.damage_class or '—'}")
    if "description" in enabled_kinds:
        lines.append(f"{s['review_description']}: {session.infra_description or '—'}")
    if "debris" in enabled_kinds:
        lines.append(f"{s['review_debris']}: {session.debris or '—'}")
    if "infra_type" in enabled_kinds:
        infra = ", ".join(session.infra_type or []) or "—"
        if session.infra_type_other:
            infra += f" ({session.infra_type_other})"
        lines.append(f"{s['review_infra']}: {infra}")
    if "crisis_nature" in enabled_kinds:
        nature = session.crisis_nature or "—"
        if session.crisis_nature_other:
            nature += f" ({session.crisis_nature_other})"
        lines.append(f"{s['review_nature']}: {nature}")
    for slot in plan:
        if not slot.is_generic:
            continue
        answer = session.generic_answers.get(slot.target)
        shown = ", ".join(answer) if isinstance(answer, list) else (answer or "—")
        lines.append(f"{slot.target}: {shown}")
    if session.location is not None:
        lat, lng = session.location
        lines.append(f"{s['review_location']}: {lat:.5f}, {lng:.5f}")
    elif session.route_description:
        lines.append(f"{s['review_route']}: {session.route_description}")
    else:
        lines.append(f"{s['review_location']}: —")
    return "\n".join(lines)


__all__ = ["TemplateConversationFlow"]
