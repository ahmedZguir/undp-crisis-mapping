"""Resident-facing copy shared by the WhatsApp LLM and template flows and their webhooks."""

from __future__ import annotations

from dataclasses import dataclass

from api.channels.languages import Lang, normalize_lang


@dataclass(frozen=True)
class Strings:
    submitted: str  # str.format template with a {ref} placeholder
    # Never echo exception text to residents.
    flow_failed: str


_TABLE: dict[Lang, Strings] = {
    "en": Strings(
        submitted="Thank you. Your report has been submitted. Reference code: {ref}",
        flow_failed="Sorry, something went wrong on our side. Please try again in a moment.",
    ),
    "ar": Strings(
        submitted="شكراً لك. تم إرسال بلاغك بنجاح. الرقم المرجعي: {ref}",
        flow_failed="عذراً، حدث خطأ من جهتنا. يرجى المحاولة مرة أخرى بعد قليل.",
    ),
    "es": Strings(
        submitted="Gracias. Su informe ha sido enviado. Código de referencia: {ref}",
        flow_failed=(
            "Lo sentimos, algo salió mal de nuestro lado. Inténtelo de nuevo en un momento."
        ),
    ),
    "fr": Strings(
        submitted="Merci. Votre rapport a été envoyé. Code de référence : {ref}",
        flow_failed="Désolé, une erreur s'est produite de notre côté. Réessayez dans un instant.",
    ),
    "ru": Strings(
        submitted="Спасибо. Ваше сообщение отправлено. Код: {ref}",
        flow_failed="Извините, у нас произошла ошибка. Попробуйте ещё раз через мгновение.",
    ),
    "zh": Strings(
        submitted="谢谢。您的报告已提交。参考编号：{ref}",
        flow_failed="抱歉，我们这边出了点问题。请稍后再试。",
    ),
}


def strings_for(lang: str) -> Strings:
    return _TABLE[normalize_lang(lang)]
