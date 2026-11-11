"""Conservative arithmetic for explicitly stated CNY monthly compensation.

``parse_salary`` returns a frozen ``Salary`` value. Its monetary fields stay
empty for annual, hourly, foreign-currency, or ambiguous salary strings. Annual
reference amounts require both a parsed monthly amount and an explicit number
of salary payments. No exchange rates, tax assumptions, or benefit values enter
the calculation.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Optional, Tuple
import unicodedata


_NUMBER = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d{1,3})?|\d+(?:\.\d{1,3})?)"
_AMOUNT = re.compile(
    rf"(?<![\d.,+\-])(?P<low>{_NUMBER})\s*(?P<low_unit>[kK万千]元?|元)?\s*"
    rf"(?:[-~～至到–—]\s*(?P<high>{_NUMBER})\s*(?P<high_unit>[kK万千]元?|元)?)?"
    r"(?![\d.,])"
)
_MONTHS = re.compile(
    r"(?<![\d.])(?P<low>\d{1,2})\s*薪?\s*"
    r"(?:[-~～至到–—]\s*(?P<high>\d{1,2})\s*)?薪(?!\d)"
)
_CURRENCIES = (
    ("USD", r"(?<![A-Z])USD(?![A-Z])|美元|美金|US\$"),
    ("JPY", r"(?<![A-Z])JPY(?![A-Z])|日元|円"),
    ("EUR", r"(?<![A-Z])EUR(?![A-Z])|欧元|€"),
    ("GBP", r"(?<![A-Z])GBP(?![A-Z])|英镑|£"),
    ("HKD", r"(?<![A-Z])HKD(?![A-Z])|港元|港币|HK\$"),
    ("TWD", r"(?<![A-Z])TWD(?![A-Z])|新台币|台币"),
    ("KRW", r"(?<![A-Z])KRW(?![A-Z])|韩元|韩币|₩"),
    ("SGD", r"(?<![A-Z])SGD(?![A-Z])|新加坡元|新币|S\$"),
    ("AUD", r"(?<![A-Z])AUD(?![A-Z])|澳元|澳币|A\$"),
    ("CAD", r"(?<![A-Z])CAD(?![A-Z])|加元|加币|C\$"),
    ("NZD", r"(?<![A-Z])NZD(?![A-Z])|新西兰元|NZ\$"),
    ("CNY", r"(?<![A-Z])CNY(?![A-Z])|(?<![A-Z])RMB(?![A-Z])|人民币|元"),
)
_PERIODS = (
    ("month", r"月薪|月工资|每月|[/／]\s*月|\bmonthly\b|\bper\s+month\b|/\s*month\b"),
    ("year", r"年薪|年包|每年|[/／]\s*年|\bannual(?:ly)?\b|\bper\s+(?:year|annum)\b|/\s*(?:year|yr)\b"),
    ("hour", r"时薪|小时|\bhourly\b|\bper\s+hour\b|/\s*(?:hour|hr|h)\b"),
    ("day", r"日薪|每天|[/／]\s*(?:天|日)|\bdaily\b|\bper\s+day\b|/\s*day\b"),
)
PERIOD_LABELS = {"month": "月薪", "year": "年薪", "hour": "时薪", "day": "日薪",
                 "mixed": "多种计薪周期", "unknown": "待确认"}


@dataclass(frozen=True)
class Salary:
    raw: str
    currency: str = "unknown"
    period: str = "unknown"
    monthly_min: Optional[Decimal] = None
    monthly_max: Optional[Decimal] = None
    months_min: Optional[int] = None
    months_max: Optional[int] = None
    tax_basis: str = "待确认"
    issues: Tuple[str, ...] = ()

    @property
    def annual_min(self):
        if self.monthly_min is not None and self.months_min is not None:
            return self.monthly_min * self.months_min
        return None

    @property
    def annual_max(self):
        if self.monthly_max is not None and self.months_max is not None:
            return self.monthly_max * self.months_max
        return None


def format_range(low, high):
    """Format Decimal amounts without changing their precision or units."""
    if low is None or high is None:
        return "待确认"

    def number(value):
        return format(value, ",f").rstrip("0").rstrip(".") if value % 1 else format(value, ",.0f")

    return number(low) if low == high else number(low) + "–" + number(high)


def parse_salary(value):
    """Parse a salary string while keeping undeclared units and pay months unknown."""
    raw = "" if value is None else str(value).strip()
    if not raw or re.fullmatch(r"(?:薪资)?面议|待定|待确认|negotiable", raw, flags=re.I):
        return Salary(raw, issues=("薪资待确认",))
    if len(raw) > 2000:
        return Salary(raw, issues=("薪资原文过长，需人工核对",))

    text = unicodedata.normalize("NFKC", raw).replace("−", "-")
    currencies = []
    amounts = text
    for currency, pattern in _CURRENCIES:
        if re.search(pattern, amounts, flags=re.I):
            currencies.append(currency)
            amounts = re.sub(pattern, lambda match: "元" if match[0] == "元" else " ", amounts, flags=re.I)
    currency = currencies[0] if len(currencies) == 1 else "mixed" if currencies else "unknown"
    periods = [period for period, pattern in _PERIODS if re.search(pattern, text, re.I)]
    period = periods[0] if len(periods) == 1 else "mixed" if periods else "unknown"
    bases = [basis for basis in ("税前", "税后") if basis in text]
    tax_basis = bases[0] if len(bases) == 1 else "待确认"

    issues = []
    if currency == "unknown":
        issues.append("币种待确认")
    elif currency == "mixed":
        issues.append("存在多种币种，需核对原文")
    elif currency != "CNY":
        issues.append("外币金额保留原文")
    if period == "unknown":
        issues.append("计薪周期待确认")
    elif period != "month":
        issues.append("年薪、时薪与日薪按原文核对" if period != "mixed" else "计薪周期冲突，需核对原文")
    if len(bases) > 1:
        issues.append("税前与税后口径冲突，需核对原文")

    month_matches = list(_MONTHS.finditer(amounts))
    months_min = months_max = None
    if len(month_matches) == 1:
        match = month_matches[0]
        low = int(match["low"])
        high = int(match["high"] or match["low"])
        conditional = re.search(r"至多|最多|最高|至少|保底|可能|视|可达|不超过|上限|下限", amounts)
        conditional = conditional or re.match(r"\s*(?:起|以上|以下|以内|左右|\+)", amounts[match.end():])
        if 1 <= low <= high <= 60 and not conditional:
            months_min, months_max = low, high
    if months_min is None:
        issues.append("发薪月数待确认，年额待确认")
    amounts = _MONTHS.sub(" ", amounts)

    monthly_min = monthly_max = None
    amount_matches = list(_AMOUNT.finditer(amounts))
    if currency == "CNY" and period == "month" and len(bases) <= 1:
        if len(amount_matches) == 1:
            match = amount_matches[0]
            multiplier = {"": Decimal(1), "k": Decimal(1000), "千": Decimal(1000), "万": Decimal(10000)}
            low_unit = (match["low_unit"] or match["high_unit"] or "").lower().replace("元", "")
            high_unit = (match["high_unit"] or "").lower().replace("元", "")
            if match["high"] is None:
                high_unit = low_unit
            try:
                low = Decimal(match["low"].replace(",", "")) * multiplier[low_unit]
                high = Decimal((match["high"] or match["low"]).replace(",", "")) * multiplier[high_unit]
                prefix = amounts[max(0, match.start() - 8):match.start()]
                suffix = re.sub(_PERIODS[0][1], "", amounts[match.end():], flags=re.I)
                open_range = re.search(r"至多|最多|最高|至少|可达|不超过|[≥≤<>]", prefix)
                open_range = open_range or re.match(r"\s*(?:起|以上|以下|\+|[≥≤<>])", suffix)
                remaining_numbers = re.search(r"\d", amounts[:match.start()] + amounts[match.end():])
                if 0 < low <= high <= Decimal("1000000000") and not open_range and not remaining_numbers:
                    monthly_min, monthly_max = low, high
            except InvalidOperation:
                pass
        if monthly_min is None:
            issues.append("月薪金额或范围需核对")

    return Salary(raw, currency, period, monthly_min, monthly_max, months_min, months_max,
                  tax_basis, tuple(issues))
