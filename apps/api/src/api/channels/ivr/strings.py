"""Spoken prompts for the IVR walker; English fills any missing key.

``_SAY_LANG`` picks the Amazon Polly voice (``arb`` is Modern Standard Arabic,
``zh-CN`` Mandarin). The language picker reads each option in its own voice so
a caller recognises their language without understanding the others.
"""

from __future__ import annotations

from api.channels.languages import normalize_lang, resolve_strings

# BCP-47 / Polly tags for TwiML <Say language="...">.
_SAY_LANG: dict[str, str] = {
    "en": "en-US",
    "ar": "arb",
    "es": "es-ES",
    "fr": "fr-FR",
    "ru": "ru-RU",
    "zh": "zh-CN",
}


DEFAULT_SAY_LANG = "en-US"


def say_lang(lang: str | None) -> str:
    return _SAY_LANG.get(normalize_lang(lang), DEFAULT_SAY_LANG)


_STRINGS: dict[str, dict[str, str]] = {
    "intro": {
        "en": (
            "Welcome to the RASID damage reporting service. "
            "By continuing, you agree to our privacy policy."
        ),
        "ar": (
            "مرحبًا بكم في خدمة الإبلاغ عن الأضرار RASID. "
            "بالمتابعة، أنت توافق على سياسة الخصوصية الخاصة بنا."
        ),
        "es": (
            "Bienvenido al servicio de reporte de daños RASID. "
            "Al continuar, acepta nuestra política de privacidad."
        ),
        "fr": (
            "Bienvenue au service de signalement de dommages RASID. "
            "En continuant, vous acceptez notre politique de confidentialité."
        ),
        "ru": (
            "Добро пожаловать в службу сообщений о повреждениях RASID. "
            "Продолжая, вы соглашаетесь с нашей политикой конфиденциальности."
        ),
        "zh": "欢迎使用 RASID 损害报告服务。继续即表示您同意我们的隐私政策。",
    },
    # Spoken in its own language's voice; {n} is its 1-based position in SUPPORTED_LANGS.
    "lang_self_prompt": {
        "en": "For English, press {n}.",
        "ar": "للغة العربية، اضغط {n}.",
        "es": "Para español, pulse {n}.",
        "fr": "Pour le français, appuyez sur {n}.",
        "ru": "Для русского языка нажмите {n}.",
        "zh": "中文，请按 {n}。",
    },
    "ask_crisis": {
        "en": "Which event are you reporting?",
        "ar": "ما هو الحدث الذي تبلغ عنه؟",
        "es": "¿Qué evento está reportando?",
        "fr": "Quel événement signalez-vous ?",
        "ru": "О каком событии вы сообщаете?",
        "zh": "您要报告哪个事件？",
    },
    "no_active_crises": {
        "en": "There are no active events to report against right now. Goodbye.",
        "ar": "لا توجد أحداث نشطة للإبلاغ عنها حاليًا. مع السلامة.",
        "es": "No hay eventos activos para reportar en este momento. Adiós.",
        "fr": "Aucun événement actif à signaler pour le moment. Au revoir.",
        "ru": "Сейчас нет активных событий для сообщения. До свидания.",
        "zh": "目前没有可报告的活动事件。再见。",
    },
    "ask_damage": {
        "en": "How badly is the building damaged?",
        "ar": "ما مدى الضرر الذي لحق بالمبنى؟",
        "es": "¿Qué tan dañado está el edificio?",
        "fr": "Quel est le niveau de dégâts du bâtiment ?",
        "ru": "Насколько повреждено здание?",
        "zh": "建筑物的损坏程度如何？",
    },
    "ask_description": {
        "en": (
            "After the beep, describe the damage and the building in a few words. "
            "This replaces a photo, so please be specific. Press pound when you are done."
        ),
        "ar": (
            "بعد سماع الصافرة، صف الضرر والمبنى بإيجاز. هذا يحل محل الصورة، لذا يرجى التحديد. "
            "اضغط مفتاح المربع عند الانتهاء."
        ),
        "es": (
            "Después del tono, describa el daño y el edificio en pocas palabras. "
            "Esto reemplaza una foto, así que sea específico. Pulse la tecla numeral cuando termine."
        ),
        "fr": (
            "Après le bip, décrivez les dégâts et le bâtiment en quelques mots. "
            "Cela remplace une photo, soyez donc précis. "
            "Appuyez sur la touche dièse lorsque vous avez terminé."
        ),
        "ru": (
            "После сигнала опишите повреждения и здание в нескольких словах. "
            "Это заменяет фотографию, поэтому будьте конкретны. "
            "Нажмите клавишу решётки, когда закончите."
        ),
        "zh": (
            "提示音后，请用几句话描述损害和建筑物。这将代替照片，请尽量具体。完成后请按井号键。"
        ),
    },
    "ask_route": {
        "en": (
            "After the beep, say how responders can find the building: "
            "the address or directions, the street, district, and any landmarks. "
            "Press pound when you are done."
        ),
        "ar": (
            "بعد سماع الصافرة، اذكر كيف يمكن لفرق الاستجابة العثور على المبنى: "
            "العنوان أو الاتجاهات، الشارع والحي وأي معالم. اضغط مفتاح المربع عند الانتهاء."
        ),
        "es": (
            "Después del tono, diga cómo pueden los equipos de respuesta encontrar el edificio: "
            "la dirección o cómo llegar, la calle, el distrito y cualquier punto de referencia. "
            "Pulse la tecla numeral cuando termine."
        ),
        "fr": (
            "Après le bip, indiquez comment les secours peuvent trouver le bâtiment : "
            "l'adresse ou l'itinéraire, la rue, le quartier et tout point de repère. "
            "Appuyez sur la touche dièse lorsque vous avez terminé."
        ),
        "ru": (
            "После сигнала скажите, как спасатели могут найти здание: "
            "адрес или ориентиры, улицу, район и любые приметные места. "
            "Нажмите клавишу решётки, когда закончите."
        ),
        "zh": (
            "提示音后，请说明救援人员如何找到这座建筑："
            "地址或路线、街道、区域以及任何地标。完成后请按井号键。"
        ),
    },
    "ask_debris": {
        "en": "Is there debris blocking access?",
        "ar": "هل توجد أنقاض تعيق الوصول؟",
        "es": "¿Hay escombros que bloquean el acceso?",
        "fr": "Des débris bloquent-ils l'accès ?",
        "ru": "Есть ли обломки, блокирующие доступ?",
        "zh": "是否有碎片阻挡通道？",
    },
    "ask_infra": {
        "en": "What type of structure is it?",
        "ar": "ما نوع المبنى؟",
        "es": "¿Qué tipo de estructura es?",
        "fr": "De quel type de structure s'agit-il ?",
        "ru": "Какой это тип сооружения?",
        "zh": "这是什么类型的建筑？",
    },
    "ask_infra_other": {
        "en": "After the beep, describe the structure type. Press pound when you are done.",
        "ar": "بعد سماع الصافرة، صف نوع المبنى. اضغط مفتاح المربع عند الانتهاء.",
        "es": "Después del tono, describa el tipo de estructura. Pulse la tecla numeral cuando termine.",
        "fr": (
            "Après le bip, décrivez le type de structure. "
            "Appuyez sur la touche dièse lorsque vous avez terminé."
        ),
        "ru": ("После сигнала опишите тип сооружения. Нажмите клавишу решётки, когда закончите."),
        "zh": "提示音后，请描述建筑类型。完成后请按井号键。",
    },
    "ask_nature": {
        "en": "What caused the damage?",
        "ar": "ما سبب الضرر؟",
        "es": "¿Qué causó el daño?",
        "fr": "Qu'est-ce qui a causé les dégâts ?",
        "ru": "Что вызвало повреждение?",
        "zh": "是什么造成了损害？",
    },
    "ask_nature_other": {
        "en": "After the beep, describe what caused the damage. Press pound when you are done.",
        "ar": "بعد سماع الصافرة، صف سبب الضرر. اضغط مفتاح المربع عند الانتهاء.",
        "es": "Después del tono, describa qué causó el daño. Pulse la tecla numeral cuando termine.",
        "fr": (
            "Après le bip, décrivez ce qui a causé les dégâts. "
            "Appuyez sur la touche dièse lorsque vous avez terminé."
        ),
        "ru": (
            "После сигнала опишите, что вызвало повреждение. "
            "Нажмите клавишу решётки, когда закончите."
        ),
        "zh": "提示音后，请描述造成损害的原因。完成后请按井号键。",
    },
    "press_for": {
        "en": "Press {n} for {label}.",
        "ar": "اضغط {n} لـ {label}.",
        "es": "Pulse {n} para {label}.",
        "fr": "Appuyez sur {n} pour {label}.",
        "ru": "Нажмите {n} для: {label}.",
        "zh": "{label}，请按 {n}。",
    },
    "to_skip": {
        "en": "To skip this question, press 0.",
        "ar": "لتخطي هذا السؤال، اضغط 0.",
        "es": "Para omitir esta pregunta, pulse 0.",
        "fr": "Pour ignorer cette question, appuyez sur 0.",
        "ru": "Чтобы пропустить этот вопрос, нажмите 0.",
        "zh": "要跳过此问题，请按 0。",
    },
    "generic_record_hint": {
        "en": "After the beep, say your answer. Press pound when you are done.",
        "ar": "بعد سماع الصافرة، قل إجابتك. اضغط مفتاح المربع عند الانتهاء.",
        "es": "Después del tono, diga su respuesta. Pulse la tecla numeral cuando termine.",
        "fr": (
            "Après le bip, dites votre réponse. "
            "Appuyez sur la touche dièse lorsque vous avez terminé."
        ),
        "ru": "После сигнала скажите ваш ответ. Нажмите клавишу решётки, когда закончите.",
        "zh": "提示音后，请说出您的答案。完成后请按井号键。",
    },
    "review_header": {
        "en": "Here is your report.",
        "ar": "إليك ملخص بلاغك.",
        "es": "Este es su reporte.",
        "fr": "Voici votre signalement.",
        "ru": "Вот ваше сообщение.",
        "zh": "以下是您的报告。",
    },
    "review_question": {
        "en": "To submit, press 1. To start over, press 2.",
        "ar": "للإرسال اضغط 1. للبدء من جديد اضغط 2.",
        "es": "Para enviar, pulse 1. Para empezar de nuevo, pulse 2.",
        "fr": "Pour envoyer, appuyez sur 1. Pour recommencer, appuyez sur 2.",
        "ru": "Чтобы отправить, нажмите 1. Чтобы начать заново, нажмите 2.",
        "zh": "提交请按 1。重新开始请按 2。",
    },
    "label_crisis": {
        "en": "Event",
        "ar": "الحدث",
        "es": "Evento",
        "fr": "Événement",
        "ru": "Событие",
        "zh": "事件",
    },
    "label_damage": {
        "en": "Damage",
        "ar": "الضرر",
        "es": "Daño",
        "fr": "Dégâts",
        "ru": "Повреждение",
        "zh": "损害",
    },
    "label_debris": {
        "en": "Debris",
        "ar": "الأنقاض",
        "es": "Escombros",
        "fr": "Débris",
        "ru": "Обломки",
        "zh": "碎片",
    },
    "label_infra": {
        "en": "Structure",
        "ar": "المبنى",
        "es": "Estructura",
        "fr": "Structure",
        "ru": "Сооружение",
        "zh": "建筑",
    },
    "label_nature": {
        "en": "Cause",
        "ar": "السبب",
        "es": "Causa",
        "fr": "Cause",
        "ru": "Причина",
        "zh": "原因",
    },
    "review_recorded": {
        "en": "Your spoken answers were recorded.",
        "ar": "تم تسجيل إجاباتك الصوتية.",
        "es": "Sus respuestas habladas fueron grabadas.",
        "fr": "Vos réponses vocales ont été enregistrées.",
        "ru": "Ваши голосовые ответы записаны.",
        "zh": "您的语音回答已录制。",
    },
    "submitted": {
        "en": "Thank you. Your report has been received. Your reference is {ref}. Goodbye.",
        "ar": "شكرًا لك. تم استلام بلاغك. رقمك المرجعي هو {ref}. مع السلامة.",
        "es": "Gracias. Su reporte ha sido recibido. Su referencia es {ref}. Adiós.",
        "fr": "Merci. Votre signalement a été reçu. Votre référence est {ref}. Au revoir.",
        "ru": "Спасибо. Ваше сообщение получено. Ваш номер {ref}. До свидания.",
        "zh": "谢谢。您的报告已收到。您的参考号是 {ref}。再见。",
    },
    # Background submit: no reference exists yet.
    "received": {
        "en": "Thank you. Your report has been received and will be processed shortly. Goodbye.",
        "ar": "شكرًا لك. تم استلام بلاغك وستتم معالجته قريبًا. مع السلامة.",
        "es": "Gracias. Su reporte ha sido recibido y será procesado en breve. Adiós.",
        "fr": "Merci. Votre signalement a été reçu et sera traité sous peu. Au revoir.",
        "ru": "Спасибо. Ваше сообщение получено и скоро будет обработано. До свидания.",
        "zh": "谢谢。您的报告已收到，将很快处理。再见。",
    },
    "submit_failed": {
        "en": "Sorry, something went wrong saving your report. Please try again later. Goodbye.",
        "ar": "عذرًا، حدث خطأ أثناء حفظ بلاغك. يرجى المحاولة لاحقًا. مع السلامة.",
        "es": (
            "Lo siento, ocurrió un error al guardar su reporte. "
            "Inténtelo de nuevo más tarde. Adiós."
        ),
        "fr": (
            "Désolé, une erreur s'est produite lors de l'enregistrement de votre signalement. "
            "Veuillez réessayer plus tard. Au revoir."
        ),
        "ru": (
            "Извините, при сохранении сообщения произошла ошибка. "
            "Пожалуйста, попробуйте позже. До свидания."
        ),
        "zh": "抱歉，保存报告时出错。请稍后重试。再见。",
    },
    "goodbye": {
        "en": "Goodbye.",
        "ar": "مع السلامة.",
        "es": "Adiós.",
        "fr": "Au revoir.",
        "ru": "До свидания.",
        "zh": "再见。",
    },
    "session_expired": {
        "en": "Sorry, your session timed out. Please call again to start over. Goodbye.",
        "ar": "عذرًا، انتهت مهلة جلستك. يرجى الاتصال مرة أخرى للبدء من جديد. مع السلامة.",
        "es": "Lo siento, su sesión expiró. Vuelva a llamar para empezar de nuevo. Adiós.",
        "fr": "Désolé, votre session a expiré. Veuillez rappeler pour recommencer. Au revoir.",
        "ru": "Извините, время сеанса истекло. Перезвоните, чтобы начать заново. До свидания.",
        "zh": "抱歉，您的会话已超时。请再次拨打以重新开始。再见。",
    },
    # Spoken labels; values stay canonical English on the wire.
    "damage_minimal": {
        "en": "minimal damage",
        "ar": "ضرر طفيف",
        "es": "daño mínimo",
        "fr": "dégâts minimes",
        "ru": "незначительные повреждения",
        "zh": "轻微损害",
    },
    "damage_partial": {
        "en": "partial damage",
        "ar": "ضرر جزئي",
        "es": "daño parcial",
        "fr": "dégâts partiels",
        "ru": "частичные повреждения",
        "zh": "部分损害",
    },
    "damage_complete": {
        "en": "complete damage",
        "ar": "ضرر كامل",
        "es": "daño total",
        "fr": "dégâts totaux",
        "ru": "полные повреждения",
        "zh": "完全损毁",
    },
    "debris_yes": {
        "en": "yes",
        "ar": "نعم",
        "es": "sí",
        "fr": "oui",
        "ru": "да",
        "zh": "是",
    },
    "debris_no": {
        "en": "no",
        "ar": "لا",
        "es": "no",
        "fr": "non",
        "ru": "нет",
        "zh": "否",
    },
    "debris_unknown": {
        "en": "not sure",
        "ar": "غير متأكد",
        "es": "no estoy seguro",
        "fr": "pas sûr",
        "ru": "не уверен",
        "zh": "不确定",
    },
    "nature_other": {
        "en": "Other",
        "ar": "أخرى",
        "es": "Otro",
        "fr": "Autre",
        "ru": "Другое",
        "zh": "其他",
    },
    "infra_residential": {
        "en": "residential",
        "ar": "سكني",
        "es": "residencial",
        "fr": "résidentiel",
        "ru": "жилое",
        "zh": "住宅",
    },
    "infra_commercial": {
        "en": "commercial",
        "ar": "تجاري",
        "es": "comercial",
        "fr": "commercial",
        "ru": "коммерческое",
        "zh": "商业",
    },
    "infra_government": {
        "en": "government",
        "ar": "حكومي",
        "es": "gubernamental",
        "fr": "gouvernemental",
        "ru": "государственное",
        "zh": "政府",
    },
    "infra_utility": {
        "en": "utility",
        "ar": "مرافق",
        "es": "servicios públicos",
        "fr": "service public",
        "ru": "коммунальное",
        "zh": "公用事业",
    },
    "infra_transport": {
        "en": "transport",
        "ar": "نقل",
        "es": "transporte",
        "fr": "transport",
        "ru": "транспорт",
        "zh": "交通",
    },
    "infra_community": {
        "en": "community",
        "ar": "مجتمعي",
        "es": "comunitario",
        "fr": "communautaire",
        "ru": "общественное",
        "zh": "社区",
    },
    "infra_public_spaces": {
        "en": "public space",
        "ar": "مساحة عامة",
        "es": "espacio público",
        "fr": "espace public",
        "ru": "общественное пространство",
        "zh": "公共空间",
    },
    "infra_other": {
        "en": "other",
        "ar": "أخرى",
        "es": "otro",
        "fr": "autre",
        "ru": "другое",
        "zh": "其他",
    },
}


def strings(lang: str) -> dict[str, str]:
    return resolve_strings(_STRINGS, lang)
