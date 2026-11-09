"""Оценка поставщиков по 100-балльной системе."""
from __future__ import annotations

from enum import Enum

CRITERIA: dict[str, tuple[int, str]] = {
    "origin_confirmed": (20, "Подтверждённое происхождение товара"),
    "resale_rights": (25, "Право коммерческой перепродажи"),
    "contract_documents": (15, "Договор и закрывающие документы"),
    "api_delivery": (15, "API для автоматической выдачи"),
    "product_verified": (10, "Подтверждённая работоспособность товара"),
    "refund_terms": (10, "Условия возврата и компенсаций"),
    "territory_ok": (5, "Территориальная совместимость"),
}

assert sum(points for points, _ in CRITERIA.values()) == 100


class SupplierDecision(str, Enum):
    CANDIDATE = "candidate"                  # 90–100
    REQUEST_DOCUMENTS = "request_documents"  # 75–89
    REJECTED = "rejected"                    # < 75 или стоп-фактор


def score(checks: dict[str, bool]) -> int:
    return sum(points for key, (points, _) in CRITERIA.items() if checks.get(key))


def evaluate(checks: dict[str, bool]) -> tuple[int, SupplierDecision, str]:
    total = score(checks)
    if not checks.get("resale_rights"):
        return total, SupplierDecision.REJECTED, "Отказ: нет права перепродажи."
    if not checks.get("origin_confirmed"):
        return total, SupplierDecision.REJECTED, "Отказ: нет подтверждения происхождения."
    if total >= 90:
        return total, SupplierDecision.CANDIDATE, "Кандидат на подключение после юридической проверки."
    if total >= 75:
        return total, SupplierDecision.REQUEST_DOCUMENTS, "Запросить дополнительные документы."
    return total, SupplierDecision.REJECTED, "Отказ: менее 75 баллов."
