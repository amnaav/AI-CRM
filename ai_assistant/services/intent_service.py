import re
from typing import Tuple, List, Dict, Any, Optional
from accounts.models import CustomUser


# Canonical Intent Identifiers
INTENT_CRM_SUMMARY = 'CRM_SUMMARY'
INTENT_CUSTOMERS_SUMMARY = 'CUSTOMERS_SUMMARY'
INTENT_LEAD_PIPELINE_SUMMARY = 'LEAD_PIPELINE_SUMMARY'
INTENT_HIGH_PRIORITY_LEADS = 'HIGH_PRIORITY_LEADS'
INTENT_LEADS_NEEDING_FOLLOWUP = 'LEADS_NEEDING_FOLLOWUP'
INTENT_REVENUE_SUMMARY = 'REVENUE_SUMMARY'
INTENT_COLLECTED_OUTSTANDING = 'COLLECTED_OUTSTANDING'
INTENT_STOCK_ALERTS = 'STOCK_ALERTS'
INTENT_REMINDERS_SUMMARY = 'REMINDERS_SUMMARY'
INTENT_STAFF_PERFORMANCE_ALL = 'STAFF_PERFORMANCE_ALL'
INTENT_FORECAST_EXPLANATION = 'FORECAST_EXPLANATION'

INTENT_STAFF_MY_CUSTOMERS = 'STAFF_MY_CUSTOMERS'
INTENT_STAFF_MY_LEADS = 'STAFF_MY_LEADS'
INTENT_STAFF_MY_HIGH_PRIORITY_LEADS = 'STAFF_MY_HIGH_PRIORITY_LEADS'
INTENT_STAFF_MY_FOLLOWUPS = 'STAFF_MY_FOLLOWUPS'
INTENT_STAFF_MY_REMINDERS = 'STAFF_MY_REMINDERS'
INTENT_STAFF_MY_PERFORMANCE = 'STAFF_MY_PERFORMANCE'
INTENT_STAFF_MY_INVOICES = 'STAFF_MY_INVOICES'

INTENT_CUSTOMER_MY_PROFILE = 'CUSTOMER_MY_PROFILE'
INTENT_CUSTOMER_MY_SALES = 'CUSTOMER_MY_SALES'
INTENT_CUSTOMER_MY_INVOICES = 'CUSTOMER_MY_INVOICES'
INTENT_CUSTOMER_MY_BALANCE = 'CUSTOMER_MY_BALANCE'
INTENT_CUSTOMER_MY_PAYMENTS = 'CUSTOMER_MY_PAYMENTS'
INTENT_CUSTOMER_MY_REMINDERS = 'CUSTOMER_MY_REMINDERS'

INTENT_PRODUCT_RECOMMENDATIONS = 'PRODUCT_RECOMMENDATIONS'
INTENT_CATALOG_BROWSE = 'CATALOG_BROWSE'
INTENT_HELP_INTENTS = 'HELP_INTENTS'
INTENT_UNSUPPORTED = 'UNSUPPORTED'

MAX_QUERY_LENGTH = 200


def sanitize_query(raw_text: Optional[str]) -> str:
    """
    Normalizes query string:
    - Strips non-printable ASCII control characters (< 32, 127).
    - Collapses consecutive whitespace characters.
    - Clamps length to MAX_QUERY_LENGTH.
    """
    if not raw_text:
        return ""
    # Strip control chars
    clean_chars = [ch for ch in str(raw_text) if ord(ch) >= 32 and ord(ch) != 127]
    cleaned = "".join(clean_chars)
    # Collapse whitespace
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned[:MAX_QUERY_LENGTH]


# Intent Rule Definitions:
# Each rule: (intent_id, regex_pattern, priority, allowed_roles)
# Priority: higher integer means evaluated earlier.
INTENT_RULES: List[Tuple[str, str, int, List[str]]] = [
    # --- Highest Priority Specific Compound Queries ---
    (
        INTENT_STAFF_MY_HIGH_PRIORITY_LEADS,
        r'\b(my\s+high\s+priority\s+leads|my\s+urgent\s+leads|my\s+hot\s+leads|my\s+top\s+leads)\b',
        100,
        [CustomUser.Role.SALES_STAFF]
    ),
    (
        INTENT_HIGH_PRIORITY_LEADS,
        r'\b(high\s+priority\s+leads|urgent\s+leads|hot\s+leads|top\s+leads|high\s+score\s+leads)\b',
        95,
        [CustomUser.Role.ADMIN]
    ),
    (
        INTENT_STAFF_MY_FOLLOWUPS,
        r'\b(my\s+follow[\s-]?ups?|my\s+stale\s+leads|my\s+leads?\s+needing\s+follow[\s-]?up|my\s+leads?\s+to\s+follow[\s-]?up)\b',
        90,
        [CustomUser.Role.SALES_STAFF]
    ),
    (
        INTENT_LEADS_NEEDING_FOLLOWUP,
        r'\b(leads?\s+needing\s+follow[\s-]?up|leads?\s+requiring\s+follow[\s-]?up|follow[\s-]?up\s+leads?|stale\s+leads?|inactive\s+leads?|stale\s+pipeline)\b',
        88,
        [CustomUser.Role.ADMIN]
    ),
    (
        INTENT_LEAD_PIPELINE_SUMMARY,
        r'\b(lead\s+pipeline|leads\s+pipeline|pipeline\s+summary|all\s+leads|lead\s+stages|pipeline\s+breakdown)\b',
        85,
        [CustomUser.Role.ADMIN]
    ),
    (
        INTENT_STAFF_MY_LEADS,
        r'\b(my\s+leads?|assigned\s+leads?|my\s+pipeline)\b',
        82,
        [CustomUser.Role.SALES_STAFF]
    ),
    (
        INTENT_FORECAST_EXPLANATION,
        r'\b(sales?\s+forecast|forecast\s+explanation|forecast\s+method|revenue\s+forecast|ai\s+forecast|personal\s+forecast|my\s+forecast|explain\s+forecast)\b',
        80,
        [CustomUser.Role.ADMIN, CustomUser.Role.SALES_STAFF]
    ),
    (
        INTENT_PRODUCT_RECOMMENDATIONS,
        r'\b(recommendations?|product\s+recommendations?|recommend\s+products?|suggest\s+products?|recommended\s+products?|recommended\s+for\s+me|customer\s+recommendations?)\b',
        78,
        [CustomUser.Role.ADMIN, CustomUser.Role.SALES_STAFF, CustomUser.Role.CUSTOMER]
    ),
    (
        INTENT_STOCK_ALERTS,
        r'\b(stock\s+alerts?|low\s+stock|out\s+of\s+stock|inventory\s+alerts?|reorder\s+levels?|stock\s+levels?|low\s+inventory)\b',
        75,
        [CustomUser.Role.ADMIN]
    ),
    (
        INTENT_COLLECTED_OUTSTANDING,
        r'\b(collected\s+(and|vs)\s+outstanding|collections?\s+(and|vs)\s+outstanding|collected\s+amount|outstanding\s+balance|unpaid\s+invoices?|outstanding\s+invoices?)\b',
        72,
        [CustomUser.Role.ADMIN]
    ),
    (
        INTENT_REVENUE_SUMMARY,
        r'\b(revenue\s+summary|total\s+revenue|sales\s+revenue|total\s+sales\s+amount|confirmed\s+revenue|financial\s+summary)\b',
        70,
        [CustomUser.Role.ADMIN]
    ),
    (
        INTENT_STAFF_PERFORMANCE_ALL,
        r'\b(staff\s+performance|sales\s+staff\s+performance|team\s+performance|rep\s+performance|sales\s+rep\s+performance|staff\s+targets)\b',
        68,
        [CustomUser.Role.ADMIN]
    ),
    (
        INTENT_STAFF_MY_PERFORMANCE,
        r'\b(my\s+performance|my\s+sales\s+performance|my\s+target|my\s+sales\s+numbers?|my\s+achievement|target\s+achievement)\b',
        66,
        [CustomUser.Role.SALES_STAFF]
    ),
    (
        INTENT_STAFF_MY_CUSTOMERS,
        r'\b(my\s+customers?|assigned\s+customers?|my\s+clients?|client\s+list)\b',
        65,
        [CustomUser.Role.SALES_STAFF]
    ),
    (
        INTENT_STAFF_MY_INVOICES,
        r'\b(my\s+invoices?|my\s+customer\s+invoices?|assigned\s+invoices?|invoices\s+and\s+balances?)\b',
        64,
        [CustomUser.Role.SALES_STAFF]
    ),
    (
        INTENT_STAFF_MY_REMINDERS,
        r'\b(my\s+reminders?|my\s+tasks?|my\s+overdue\s+reminders?|my\s+pending\s+tasks?|overdue\s+tasks?)\b',
        63,
        [CustomUser.Role.SALES_STAFF]
    ),
    (
        INTENT_CUSTOMERS_SUMMARY,
        r'\b(total\s+customers?|active\s+customers?|customer\s+count|customers\s+overview|all\s+customers?)\b',
        60,
        [CustomUser.Role.ADMIN]
    ),
    (
        INTENT_CRM_SUMMARY,
        r'\b(crm\s+summary|executive\s+summary|crm\s+overview|dashboard\s+summary|system\s+summary|overall\s+summary|crm\s+health)\b',
        58,
        [CustomUser.Role.ADMIN]
    ),
    (
        INTENT_REMINDERS_SUMMARY,
        r'\b(reminders?\s+summary|all\s+reminders?|pending\s+reminders?|overdue\s+reminders?|system\s+reminders?|all\s+tasks?)\b',
        56,
        [CustomUser.Role.ADMIN]
    ),

    # --- Customer Intents ---
    (
        INTENT_CUSTOMER_MY_PROFILE,
        r'\b(my\s+profile|my\s+account|account\s+details|profile\s+summary|who\s+is\s+my\s+rep|my\s+info|my\s+details)\b',
        60,
        [CustomUser.Role.CUSTOMER]
    ),
    (
        INTENT_CUSTOMER_MY_BALANCE,
        r'\b(my\s+balance|outstanding\s+balance|how\s+much\s+do\s+i\s+owe|balance\s+due|unpaid\s+amount|my\s+dues?|amount\s+owed)\b',
        58,
        [CustomUser.Role.CUSTOMER]
    ),
    (
        INTENT_CUSTOMER_MY_SALES,
        r'\b(my\s+purchases?|my\s+orders?|my\s+sales\s+orders?|order\s+history|purchase\s+history|past\s+orders?)\b',
        55,
        [CustomUser.Role.CUSTOMER]
    ),
    (
        INTENT_CUSTOMER_MY_INVOICES,
        r'\b(my\s+invoices?|my\s+bills?|billing|invoices?|invoice\s+list)\b',
        54,
        [CustomUser.Role.CUSTOMER]
    ),
    (
        INTENT_CUSTOMER_MY_PAYMENTS,
        r'\b(my\s+payments?|payment\s+receipts?|payment\s+history|receipts?|completed\s+payments?)\b',
        52,
        [CustomUser.Role.CUSTOMER]
    ),
    (
        INTENT_CUSTOMER_MY_REMINDERS,
        r'\b(my\s+reminders?|my\s+notifications?|reminders?|appointments?|scheduled\s+reminders?)\b',
        50,
        [CustomUser.Role.CUSTOMER]
    ),

    # --- Broad Catalog & General Topics ---
    (
        INTENT_CATALOG_BROWSE,
        r'\b(catalog|available\s+products?|products?\s+and\s+services?|available\s+services?|products?\s+list|view\s+catalog)\b',
        40,
        [CustomUser.Role.ADMIN, CustomUser.Role.SALES_STAFF, CustomUser.Role.CUSTOMER]
    ),
    (
        INTENT_HELP_INTENTS,
        r'\b(help|supported\s+questions?|what\s+can\s+you\s+do|commands?|options?|supported\s+topics?|help\s+me|guide)\b',
        30,
        [CustomUser.Role.ADMIN, CustomUser.Role.SALES_STAFF, CustomUser.Role.CUSTOMER]
    ),
]


def detect_intent(raw_query: str, user_role: str) -> str:
    """
    Detects the canonical intent for a sanitized query and user role.
    Uses deterministic priority order.
    Returns INTENT_UNSUPPORTED if query does not match any allowed rule for user_role.
    """
    cleaned = sanitize_query(raw_query).lower()
    if not cleaned:
        return INTENT_HELP_INTENTS

    # Sort rules strictly by priority descending
    sorted_rules = sorted(INTENT_RULES, key=lambda r: r[2], reverse=True)

    for intent_id, pattern, _priority, allowed_roles in sorted_rules:
        if re.search(pattern, cleaned, re.IGNORECASE):
            # Check role authorization
            if user_role in allowed_roles:
                return intent_id
            else:
                # If matched a pattern not permitted for this role (e.g. customer asking for admin crm summary),
                # continue checking other rules or fall back to UNSUPPORTED.
                continue

    # Fallback to general keywords if exact regex did not catch
    # Role-specific fallbacks
    if user_role == CustomUser.Role.ADMIN:
        if 'summary' in cleaned or 'overview' in cleaned:
            return INTENT_CRM_SUMMARY
        if 'customer' in cleaned:
            return INTENT_CUSTOMERS_SUMMARY
        if 'lead' in cleaned:
            return INTENT_LEAD_PIPELINE_SUMMARY
        if 'revenue' in cleaned or 'sale' in cleaned:
            return INTENT_REVENUE_SUMMARY
        if 'stock' in cleaned or 'inventory' in cleaned:
            return INTENT_STOCK_ALERTS
        if 'reminder' in cleaned or 'task' in cleaned:
            return INTENT_REMINDERS_SUMMARY
    elif user_role == CustomUser.Role.SALES_STAFF:
        if 'lead' in cleaned:
            return INTENT_STAFF_MY_LEADS
        if 'customer' in cleaned or 'client' in cleaned:
            return INTENT_STAFF_MY_CUSTOMERS
        if 'sale' in cleaned or 'performance' in cleaned or 'target' in cleaned:
            return INTENT_STAFF_MY_PERFORMANCE
        if 'invoice' in cleaned:
            return INTENT_STAFF_MY_INVOICES
        if 'reminder' in cleaned or 'task' in cleaned:
            return INTENT_STAFF_MY_REMINDERS
    elif user_role == CustomUser.Role.CUSTOMER:
        if 'order' in cleaned or 'purchase' in cleaned:
            return INTENT_CUSTOMER_MY_SALES
        if 'invoice' in cleaned or 'bill' in cleaned:
            return INTENT_CUSTOMER_MY_INVOICES
        if 'balance' in cleaned or 'due' in cleaned or 'owe' in cleaned:
            return INTENT_CUSTOMER_MY_BALANCE
        if 'payment' in cleaned or 'receipt' in cleaned:
            return INTENT_CUSTOMER_MY_PAYMENTS
        if 'profile' in cleaned or 'account' in cleaned:
            return INTENT_CUSTOMER_MY_PROFILE
        if 'recommend' in cleaned:
            return INTENT_PRODUCT_RECOMMENDATIONS

    return INTENT_UNSUPPORTED
