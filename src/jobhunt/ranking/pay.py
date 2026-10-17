"""Salary text parsing and coarse pay estimates. Parsing rules ported in spirit from career-ops (MIT)."""

import re
from dataclasses import dataclass

# shortcut: fixed rates, refresh by hand yearly; upgrade to a rates API if pay ranking looks off.
FX_TO_EUR = {"EUR": 1.0, "USD": 0.92, "GBP": 1.17, "CHF": 1.05, "SEK": 0.088, "NOK": 0.086,
             "DKK": 0.134, "PLN": 0.23, "CZK": 0.040, "CAD": 0.67, "AUD": 0.60, "SGD": 0.68}
# Gross yearly EUR, (junior, mid), software developer. Coarse by design (spec: "a coarse table is fine").
PAY_TABLE = {
    "PT": (18_000, 28_000), "ES": (24_000, 35_000), "IT": (26_000, 36_000), "FR": (36_000, 48_000),
    "DE": (50_000, 65_000), "NL": (45_000, 60_000), "BE": (42_000, 55_000), "AT": (45_000, 58_000),
    "IE": (45_000, 65_000), "LU": (50_000, 65_000), "DK": (55_000, 70_000), "SE": (42_000, 55_000),
    "FI": (40_000, 52_000), "NO": (55_000, 70_000), "IS": (50_000, 65_000), "CH": (85_000, 110_000),
    "PL": (20_000, 35_000), "CZ": (22_000, 35_000), "SK": (18_000, 28_000), "HU": (16_000, 28_000),
    "RO": (15_000, 28_000), "BG": (14_000, 25_000), "GR": (16_000, 25_000), "HR": (16_000, 26_000),
    "SI": (22_000, 32_000), "EE": (26_000, 40_000), "LV": (20_000, 32_000), "LT": (22_000, 35_000),
    "CY": (22_000, 35_000), "MT": (25_000, 35_000), "GB": (40_000, 60_000), "US": (85_000, 120_000),
    "CA": (55_000, 75_000), "AU": (55_000, 75_000), "NZ": (45_000, 65_000), "SG": (45_000, 65_000),
    "JP": (35_000, 50_000), "AE": (40_000, 60_000), "IL": (45_000, 70_000), "BR": (12_000, 22_000),
    "IN": (8_000, 16_000),
}
PAY_SOURCE = "jobhunt coarse table 2026"

# Hours per year and working days per year, for annualising day and hour rates.
_PERIOD_FACTOR = {"year": 1, "month": 12, "day": 220, "hour": 2080}
_SYMBOL_CODE = {"€": "EUR", "£": "GBP", "$": "USD"}
_PERIOD_WORDS = {
    "year": "year", "yr": "year", "annum": "year", "annual": "year", "yearly": "year",
    "pa": "year", "ano": "year", "año": "year", "jahr": "year",
    "month": "month", "mo": "month", "monthly": "month", "mês": "month", "mes": "month",
    "monat": "month",
    "day": "day", "daily": "day", "dia": "day",
    "hour": "hour", "hr": "hour", "hourly": "hour", "hora": "hour", "stunde": "hour",
}

_CUR = r"(?:[€£$]|\b(?:EUR|USD|GBP|CHF|SEK|NOK|DKK|PLN|CZK|CAD|AUD|SGD)\b)"
# grouping (45.000 / 45,000) is tried before decimal (45,5 / 45.5); optional k suffix
# grouping capped at 3 separators: an unbounded run makes int() in _num raise past 4300 digits
_NUM = r"(?:\d{1,3}(?:[.,]\d{3}){1,3}|\d+(?:[.,]\d+)?)(?:\s?[kK]\b)?"
_SEP = r"\s*(?:-|–|—|\bto\b|\ba\b)\s*"
_SALARY = re.compile(
    rf"(?P<c1>{_CUR})\s?(?P<n1>{_NUM})(?:{_SEP}(?:{_CUR}\s?)?(?P<n2>{_NUM}))?"
    rf"|(?P<n3>{_NUM})\s?(?P<c2>{_CUR})(?:{_SEP}(?P<n4>{_NUM})\s?(?:{_CUR})?)?",
    re.IGNORECASE,
)
_PERIOD = re.compile(
    r"\b(" + "|".join(_PERIOD_WORDS) + r")\b|p\.a\.", re.IGNORECASE
)


@dataclass(frozen=True)
class Salary:
    low: float
    high: float
    currency: str
    period: str  # "year" | "month" | "day" | "hour"


def _num(tok: str) -> float:
    k = tok.rstrip()[-1] in "kK"
    core = tok.rstrip().rstrip("kK").strip()
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", core):
        value = int(re.sub(r"\D", "", core))
    else:
        value = float(core.replace(",", "."))
    return value * 1000 if k else value


_MILLIONS = re.compile(r"\s?(?:m|mm|million|b|bn|billion)\b", re.IGNORECASE)
_PERIOD_NEAR = 25  # characters after the number in which its period word must appear


def parse_salary(text: str | None) -> Salary | None:
    """First currency amount that reads as pay: not millions, in range, with a period or >= 10k."""
    if not text:
        return None
    for m in _SALARY.finditer(text):
        if _MILLIONS.match(text, m.end()):
            continue
        if m.group("n1"):
            cur, low_tok, high_tok = m.group("c1"), m.group("n1"), m.group("n2")
        else:
            cur, low_tok, high_tok = m.group("c2"), m.group("n3"), m.group("n4")
        low = _num(low_tok)
        high = _num(high_tok) if high_tok else low
        currency = _SYMBOL_CODE.get(cur, cur.upper())
        period_match = _PERIOD.search(text, m.end(), m.end() + _PERIOD_NEAR)
        if period_match:
            period = _PERIOD_WORDS.get(period_match.group(0).lower(), "year")  # "p.a." is a year
        elif low >= 10_000:
            period = "year"
        else:
            continue
        salary = Salary(low, high, currency, period)
        if annual_eur(salary) is not None:
            return salary
    return None


def annual_eur(s: Salary) -> int | None:
    rate = FX_TO_EUR.get(s.currency)
    if rate is None:
        return None
    value = (s.low + s.high) / 2 * _PERIOD_FACTOR[s.period] * rate
    if not 5_000 <= value <= 1_000_000:
        return None
    return round(value)  # round(float) with no ndigits is already an int


def estimate_pay(country: str | None, level: str) -> int | None:
    row = PAY_TABLE.get(country)
    if row is None:
        return None
    return row[0] if level == "junior" else row[1]
