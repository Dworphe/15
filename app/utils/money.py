from decimal import Decimal, ROUND_HALF_EVEN, getcontext
getcontext().rounding = ROUND_HALF_EVEN

def bank_round_2(x: float) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.01")))
