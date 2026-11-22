"""Document types, as inferred from number series on 18.11 (spec §3.1). Not yet read from Hashavshevet's
type table: report R-E (slice B3) confirms them, and until then every name here is `inferred`."""

from __future__ import annotations

# DocumentID -> (role, Hebrew name)
INFERRED = {
    1: ("invoice", "חשבונית מס"),
    2: ("invoice_receipt", "חשבונית מס/קבלה"),
    3: ("credit", "חשבונית זיכוי"),
    31: ("receipt", "קבלה"),
    78: ("proforma", "חשבון עסקה"),
    88: ("own", "חשבונית עצמית"),
}
# A document is valid in every figure only with status 1; status 0 is a draft (spec §3.1, §4.1).
VALID_STATUS = 1
DRAFT_STATUS = 0


def role(doc_type) -> str:
    try:
        return INFERRED[int(doc_type)][0]
    except (KeyError, TypeError, ValueError):
        return "unknown"
