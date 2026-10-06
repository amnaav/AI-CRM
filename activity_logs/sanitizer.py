import re

SENSITIVE_MASK = "[REDACTED]"

# Comprehensive list of case-insensitive sensitive keys and patterns to redact
FORBIDDEN_KEY_PATTERNS = {
    'password', 'passwd', 'passphrase', 'new_password', 'old_password',
    'token', 'access_token', 'refresh_token', 'csrf_token', 'csrftoken',
    'cookie', 'session', 'sessionid', 'session_key',
    'authorization', 'auth', 'bearer',
    'secret', 'secret_key', 'api_key', 'private_key',
    'idempotency_key',
    'reset_link', 'password_reset', 'reset_token', 'token_url', 'activation_link',
    'card_number', 'credit_card', 'pan', 'cvv', 'cvc', 'pin', 'account_number', 'routing_number'
}

# Regex to detect token query parameters or bearer credentials inside string values
SENSITIVE_STRING_REGEX = re.compile(
    r'(?i)\b(bearer|token|sessionid|key|secret)\b(\s*[:=\s]\s*)([a-zA-Z0-9_\-\.]{8,})',
)
PAN_REGEX = re.compile(r'\b(?:\d[ -]*?){13,16}\b')


def _is_sensitive_key(key: str) -> bool:
    """Check if dictionary key matches sensitive field names."""
    if not isinstance(key, str):
        return False
    norm_key = key.lower().replace('-', '_')
    if norm_key in FORBIDDEN_KEY_PATTERNS:
        return True
    for pattern in FORBIDDEN_KEY_PATTERNS:
        if pattern in norm_key:
            return True
    return False


def _sanitize_string_value(val: str) -> str:
    """Scrub sensitive substrings like tokens or card numbers from text."""
    if not isinstance(val, str):
        return val
    # Redact explicit tokens in URLs/strings
    val = SENSITIVE_STRING_REGEX.sub(r'\1\2' + SENSITIVE_MASK, val)
    # Redact suspected payment PANs
    val = PAN_REGEX.sub(SENSITIVE_MASK, val)
    return val



def _sanitize_recursive(data):
    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            if _is_sensitive_key(str(k)):
                sanitized[k] = SENSITIVE_MASK
            else:
                sanitized[k] = _sanitize_recursive(v)
        return sanitized

    if isinstance(data, (list, tuple, set)):
        return [_sanitize_recursive(item) for item in data]

    if isinstance(data, str):
        return _sanitize_string_value(data)

    return data


def sanitize_metadata(data):
    """
    Recursively scrubs sensitive keys, credentials, tokens, session IDs,
    and payment references from metadata structures before persistence.
    If root data is not a dict, returns an empty dictionary.
    """
    if not isinstance(data, dict):
        return {}

    return _sanitize_recursive(data)

