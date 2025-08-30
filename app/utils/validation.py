from email_validator import validate_email, EmailNotValidError

def mask_card(card_number: str) -> str:
    digits = "".join(ch for ch in card_number if ch.isdigit())
    tail = digits[-4:] if len(digits) >= 4 else digits
    return f"**** **** **** {tail}" if tail else "****"

def validate_pay_account(s: str) -> bool:
    s = s.strip()
    if not (6 <= len(s) <= 64): return False
    # цифры, +, пробелы, и произвольный хвост банка (проверим длину отдельно в мастере)
    ok = all(ch.isdigit() or ch in "+ " or ch.isalpha() for ch in s)
    return ok

def validate_email_addr(addr: str) -> bool:
    try:
        validate_email(addr, allow_smtputf8=False, allow_empty_local=False)
        return True
    except EmailNotValidError:
        return False

def validate_decimal_range_step(value: float, lo: float, hi: float, step: float = 0.01) -> bool:
    if not (lo <= value <= hi): return False
    # проверка шага 0.01
    return abs(round(value * 100) - value * 100) < 1e-9
