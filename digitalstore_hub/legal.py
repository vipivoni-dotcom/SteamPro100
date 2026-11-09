"""Юридический фильтр DigitalStore Hub.

Логика основана на ограничениях 422-ФЗ (НПД): самозанятый не вправе
перепродавать товары и имущественные права и вести посредническую
деятельность (поручение, комиссия, агентские договоры).

Это технический фильтр, а не юридическая консультация: он не пропускает
заведомо неподходящие операции, но окончательное решение требует проверки.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TaxRegime(str, Enum):
    NPD = "npd"  # самозанятый
    IP = "ip"    # индивидуальный предприниматель
    OOO = "ooo"  # юридическое лицо


class ProductType(str, Enum):
    EBOOK = "ebook"
    TEMPLATE = "template"
    SPREADSHEET = "spreadsheet"
    COURSE = "course"
    SOFTWARE = "software"
    GAME_KEY = "game_key"
    LICENSE_KEY = "license_key"
    SUBSCRIPTION = "subscription"
    GIFT_CARD = "gift_card"


class Ownership(str, Enum):
    OWN_CREATED = "own_created"  # создано самим продавцом
    RESALE = "resale"            # куплено для перепродажи
    AGENCY = "agency"            # поручение / комиссия / агентский договор


# Линия A — собственные продукты, линия B — товары сторонних поставщиков.
LINE_B_TYPES = {
    ProductType.GAME_KEY,
    ProductType.LICENSE_KEY,
    ProductType.SUBSCRIPTION,
    ProductType.GIFT_CARD,
}


@dataclass(frozen=True)
class LegalDecision:
    allowed: bool
    line: str  # "A" или "B"
    message: str


def product_line(product_type: ProductType, ownership: Ownership) -> str:
    if ownership is Ownership.OWN_CREATED and product_type not in LINE_B_TYPES:
        return "A"
    return "B"


def check_product(
    product_type: ProductType,
    ownership: Ownership,
    regime: TaxRegime,
    supplier_approved: bool = False,
) -> LegalDecision:
    line = product_line(product_type, ownership)

    if line == "A":
        msg = "Предварительно допустимо: собственный цифровой продукт (линия A)."
        if product_type is ProductType.SOFTWARE and regime is TaxRegime.NPD:
            msg += (" Для ПО на НПД проверьте условия: продажа собственных прав,"
                    " без перепродажи чужих компонентов и лицензий.")
        return LegalDecision(True, line, msg)

    # Линия B
    if regime is TaxRegime.NPD:
        if ownership is Ownership.AGENCY:
            reason = "посредническая деятельность (поручение/комиссия/агентский договор)"
        elif ownership is Ownership.RESALE:
            reason = "перепродажа товаров и имущественных прав"
        else:
            reason = (f"тип «{product_type.value}» — товар стороннего правообладателя;"
                      " переименование в «услугу по выдаче кода» не меняет сущность операции")
        return LegalDecision(
            False, line,
            f"Заблокировано на НПД (422-ФЗ): {reason}. Линия B доступна только для ИП/ООО"
            " с подтверждённым правом перепродажи.",
        )

    if not supplier_approved:
        return LegalDecision(
            False, line,
            "Линия B требует поставщика со статусом «кандидат» (90+ баллов,"
            " подтверждены происхождение и право перепродажи).",
        )
    return LegalDecision(
        True, line,
        "Предварительно допустимо для линии B: поставщик прошёл проверку."
        " Требуется юридическая проверка договора.",
    )
