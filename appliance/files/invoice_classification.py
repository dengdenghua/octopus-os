"""Conservative invoice fields from already extracted, untrusted document text.

This module performs no I/O, model calls, OCR, or document extraction. A ready
classification supplies a proposed month only; the caller still owns approval,
source identity checks, and file operations.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

_SUPPORTED = frozenset({"pdf", "docx", "txt", "md", "csv", "tsv"})
_MAX_TEXT = 100_000
_MAX_EVIDENCE = 8
_DATE_LABEL = re.compile(
    r"开票日期|发票日期|\binvoice\s+date\b|\bdate\s+of\s+issue\b", re.IGNORECASE
)
_AMOUNT_LABEL = re.compile(
    r"价税合计(?:\s*\(\s*小写\s*\))?|金额合计|合计金额|应付总额|"
    r"\bgrand\s+total\b|\btotal(?:\s+amount(?:\s+due)?|\s+due)?\b",
    re.IGNORECASE,
)
_OTHER_FIELD = re.compile(
    r"付款日期|支付日期|打印日期|到期日期|发票号码|发票代码|"
    r"\b(?:payment\s+date|due\s+date|print\s+date|invoice\s+number)\b",
    re.IGNORECASE,
)
_FIELD_BOUNDARY = re.compile(
    "|".join(pattern.pattern for pattern in (_DATE_LABEL, _AMOUNT_LABEL, _OTHER_FIELD)),
    re.IGNORECASE,
)
_INVOICE = re.compile(r"发票|\binvoice\b|开票日期", re.IGNORECASE)
_ISO_DATE = re.compile(
    r"(?<!\d)(\d{4})\s*([-/.年])\s*(\d{1,2})\s*([-/.月])\s*(\d{1,2})(?:\s*日)?(?!\d)"
)
_LOCAL_DATE = re.compile(r"(?<!\d)\d{1,2}\s*[/.-]\s*\d{1,2}\s*[/.-]\s*\d{2,4}(?!\d)")
_MONTH_NAMES = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)
_MONTHS = {
    variant: index for index, month in enumerate(_MONTH_NAMES, 1) for variant in (month, month[:3])
}
_MONTH_PATTERN = "|".join(sorted(_MONTHS, key=len, reverse=True))
_ENGLISH_DATE = re.compile(
    rf"\b(?:(?P<month>{_MONTH_PATTERN})\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?[,]?\s+(?P<year>\d{{4}})"
    rf"|(?P<day_first>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<month_second>{_MONTH_PATTERN})\.?[,]?\s+(?P<year_last>\d{{4}}))\b",
    re.IGNORECASE,
)
_CURRENCY = re.compile(
    r"CNY|RMB|USD|EUR|GBP|JPY|人民币|美元|欧元|英镑|日元|元|¥|\$|€|£", re.IGNORECASE
)
_CURRENCIES = {
    "CNY": "CNY",
    "RMB": "CNY",
    "人民币": "CNY",
    "元": "CNY",
    "USD": "USD",
    "美元": "USD",
    "EUR": "EUR",
    "欧元": "EUR",
    "€": "EUR",
    "GBP": "GBP",
    "英镑": "GBP",
    "£": "GBP",
    "JPY": "JPY",
    "日元": "JPY",
}


def _short(value: str, limit: int = 100) -> str:
    return " ".join(value.split())[:limit]


def _title(name: str, text: str) -> str:
    for line in text.splitlines()[:8]:
        for cell in re.split(r"[\t|]", line):
            candidate = cell.strip(" #*")
            if (
                0 < len(candidate) <= 100
                and _INVOICE.search(candidate)
                and not _FIELD_BOUNDARY.search(candidate)
            ):
                return _short(candidate)
    basename = name.replace("\\", "/").rsplit("/", 1)[-1]
    return _short(basename.rsplit(".", 1)[0]) or "未命名票据"


def _cell(value: str) -> str:
    return value.strip(" \t:：|*#")


def _values(text: str, labels: re.Pattern[str]) -> list[tuple[str, str, int]]:
    """Read immediate labelled values, including extracted two-dimensional tables."""
    lines = text.splitlines()
    found: list[tuple[str, str, int]] = []
    table_headers: set[int] = set()
    for index, line in enumerate(lines):
        separator = "\t" if "\t" in line else "|" if line.count("|") >= 2 else None
        if separator is None:
            continue
        cells = line.strip().strip("|").split(separator)
        columns = [(column, labels.fullmatch(_cell(value))) for column, value in enumerate(cells)]
        columns = [(column, match) for column, match in columns if match is not None]
        if len(cells) < 2 or not columns:
            continue
        # A field/value table row, e.g. "开票日期\t2026-09-05", is not a header.
        if (
            len(cells) == 2
            and columns[0][0] == 0
            and not (
                _DATE_LABEL.fullmatch(_cell(cells[1])) or _AMOUNT_LABEL.fullmatch(_cell(cells[1]))
            )
        ):
            continue
        table_headers.add(index)
        for following in range(index + 1, len(lines)):
            row = lines[following].strip().strip("|")
            if not row:
                break
            if re.fullmatch(r"[\s:|\t-]+", row):
                continue
            values = row.split(separator)
            if len(values) != len(cells):
                break
            if any(_DATE_LABEL.fullmatch(_cell(value)) for value in values):
                break
            for column, match in columns:
                assert match is not None
                found.append((match.group(), _cell(values[column]), following + 1))
    for index, line in enumerate(lines):
        if index in table_headers:
            continue
        for match in labels.finditer(line):
            raw = line[match.end() :].lstrip(" \t:：|*")
            if not raw:
                # DOCX paragraphs often split the field label and its value.
                for following in lines[index + 1 : index + 3]:
                    if following.strip():
                        raw = following.lstrip(" \t:：|*")
                        break
            if boundary := _FIELD_BOUNDARY.search(raw):
                raw = raw[: boundary.start()]
            found.append((match.group(), raw.strip(), index + 1))
    return found


def _date_values(raw: str) -> tuple[list[str], str | None]:
    # Only immediately associated values qualify; prose that happens to contain
    # a later date is not evidence for the field.
    cleaned = raw.strip(" \t|*()")
    if _LOCAL_DATE.match(cleaned):
        return [], "ambiguous_invoice_date"
    matches = sorted(
        [*_ISO_DATE.finditer(cleaned), *_ENGLISH_DATE.finditer(cleaned)],
        key=lambda match: match.start(),
    )
    if not matches or matches[0].start() != 0:
        return [], "invalid_invoice_date"
    values = []
    for match in matches:
        if match.re is _ISO_DATE:
            year, first_separator, month, second_separator, day = match.groups()
            if (first_separator == "年" and second_separator != "月") or (
                first_separator != "年" and first_separator != second_separator
            ):
                return [], "invalid_invoice_date"
        else:
            fields = match.groupdict()
            year = fields["year"] or fields["year_last"]
            month = str(_MONTHS[(fields["month"] or fields["month_second"]).lower()])
            day = fields["day"] or fields["day_first"]
        try:
            values.append(date(int(year), int(month), int(day)).isoformat())
        except ValueError:
            return [], "invalid_invoice_date"
    # A second short-format value must not be ignored after one valid ISO date.
    remainder = cleaned[matches[0].end() :]
    if _LOCAL_DATE.search(remainder):
        return [], "ambiguous_invoice_date"
    return values, None


def _amount(raw: str, label: str) -> tuple[str | None, str | None, str | None]:
    tokens = list(_CURRENCY.finditer(raw))
    currencies = {
        _CURRENCIES[token.group().upper()]
        for token in tokens
        if token.group().upper() in _CURRENCIES
    }
    if (
        any(token.group() == "¥" for token in tokens)
        and not currencies
        and re.search(r"[\u4e00-\u9fff]", label)
    ):
        currencies.add("CNY")
    if len(currencies) > 1:
        return None, None, "conflicting_currencies"
    currency = next(iter(currencies), None)
    without_currency = _CURRENCY.sub("", raw).strip(" \t:：|*")
    # No expression evaluation, guessed decimal separators, rounding, or sum.
    if not re.fullmatch(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?", without_currency):
        return None, currency, "ambiguous_amount"
    try:
        number = Decimal(without_currency.replace(",", ""))
        if len(number.as_tuple().digits) > 18:
            return None, currency, "ambiguous_amount"
        return format(number.quantize(Decimal("0.01")), "f"), currency, None
    except InvalidOperation:
        return None, currency, "ambiguous_amount"


def classify_invoice(
    *,
    name: str,
    text: str | None,
    truncated: bool = False,
    format: str,
    extraction_available: bool = True,
) -> dict[str, Any]:
    """Return bounded source evidence and a conservative proposed year/month."""
    normalized = unicodedata.normalize("NFKC", text or "")
    evidence: dict[str, Any] = {
        "dateCandidates": [],
        "amountCandidates": [],
        "amountReason": None,
        "filenameDateHints": [],
    }
    for match in _ISO_DATE.finditer(name[:500]):
        values, error = _date_values(match.group())
        if not error and len(evidence["filenameDateHints"]) < 3:
            evidence["filenameDateHints"].extend(values)
    result: dict[str, Any] = {
        "status": "needs_review",
        "reason": None,
        "date": None,
        "yearMonth": None,
        "title": _title(name, normalized[:1_000]),
        "amount": None,
        "currency": None,
        "evidence": evidence,
    }
    if format.lower().lstrip(".") not in _SUPPORTED:
        result.update(status="unsupported", reason="unsupported_format")
        return result
    if not extraction_available:
        result["reason"] = "extraction_unavailable"
        return result
    if not normalized.strip():
        result["reason"] = "no_text"
        return result
    if truncated or len(normalized) > _MAX_TEXT:
        result["reason"] = "text_truncated"
        return result
    if "\x00" in normalized or "\ufffd" in normalized:
        result["reason"] = "encoding_uncertain"
        return result
    if not _INVOICE.search(normalized):
        result["reason"] = "not_invoice"
        return result

    dates: set[str] = set()
    date_error = None
    for label, raw, line in _values(normalized, _DATE_LABEL):
        values, error = _date_values(raw)
        dates.update(values)
        if error:
            date_error = error
        if len(evidence["dateCandidates"]) < _MAX_EVIDENCE:
            evidence["dateCandidates"].append(
                {
                    "label": _short(label, 40),
                    "value": values[0] if len(values) == 1 else None,
                    "snippet": _short(raw),
                    "line": line,
                    "reason": error,
                }
            )

    amounts: set[tuple[str, str | None]] = set()
    amount_error = None
    for label, raw, line in _values(normalized, _AMOUNT_LABEL):
        amount, currency, error = _amount(raw, label)
        if error:
            amount_error = error
        elif amount is not None:
            amounts.add((amount, currency))
        if len(evidence["amountCandidates"]) < _MAX_EVIDENCE:
            evidence["amountCandidates"].append(
                {
                    "label": _short(label, 40),
                    "value": amount,
                    "currency": currency,
                    "snippet": _short(raw),
                    "line": line,
                    "reason": error,
                }
            )
    if len(amounts) > 1:
        amount_error = "conflicting_amounts"
    evidence["amountReason"] = amount_error
    if len(amounts) == 1 and amount_error is None:
        result["amount"], result["currency"] = next(iter(amounts))

    if len(dates) > 1:
        result["reason"] = "conflicting_invoice_dates"
    elif date_error:
        result["reason"] = date_error
    elif not dates:
        result["reason"] = "no_invoice_date"
    else:
        chosen = next(iter(dates))
        result.update(status="ready", date=chosen, yearMonth=chosen[:7].replace("-", "/"))
    return result
