"""Validate QBO envelopes while retaining exact invoice JSON source fragments."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal, DecimalException
from typing import NotRequired, TypedDict, cast

import httpx

from stackos_connectors.errors import ConnectorError
from stackos_connectors.redaction import redact_secret_values

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_INVOICE_BYTES = 16 * 1024
_WS = re.compile(r"[ \t\n\r]*")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
type JsonValue = dict[str, JsonValue] | list[JsonValue] | str | int | Decimal | bool | None


class InvoiceRecord(TypedDict):
    native_id: str
    raw_json: str
    source_revision: NotRequired[str]
    source_updated_at: NotRequired[str]


class InvoicePage(TypedDict):
    invoices: list[InvoiceRecord]
    start_position: int
    max_results: int
    count: int
    total_count: NotRequired[int]


class CompanyInfo(TypedDict):
    realm_id: str
    company_id: str
    company_name: NotRequired[str]


def invalid_response() -> ConnectorError:
    return ConnectorError(
        "QuickBooks returned invalid evidence",
        provider_error={"reason_code": "invalid_response"},
    )


def calendar_date(value: object) -> str:
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise ValueError("invalid date")
    date.fromisoformat(value)
    return value


def _object_pairs(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        key.encode("utf-8")
        if isinstance(value, str):
            value.encode("utf-8")
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError("non-JSON number")


_DECODER = json.JSONDecoder(
    object_pairs_hook=_object_pairs,
    parse_float=Decimal,
    parse_constant=_constant,
)


def _document(
    response: httpx.Response, secrets: tuple[str, ...]
) -> tuple[str, dict[str, JsonValue]]:
    if response.status_code != 200 or len(response.content) > MAX_RESPONSE_BYTES:
        raise ValueError("invalid HTTP response")
    text = response.content.decode("utf-8")
    value = _object(_DECODER.decode(text))
    # Check both escaped source text and decoded keys/values. The registry's
    # exact-auth redaction must never silently change evidence after this point.
    if redact_secret_values(text, secrets) != text or redact_secret_values(value, secrets) != value:
        raise ValueError("credential echo")
    return text, value


def _object(value: JsonValue) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError("expected object")
    return value


def _identity(value: JsonValue) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value.strip() != value
        or len(value) > 128
    ):
        raise ValueError("invalid identity")
    return value


def _integer(value: JsonValue, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError("invalid integer")
    return value


def _skip(text: str, position: int) -> int:
    match = _WS.match(text, position)
    assert match is not None
    return match.end()


def _member_text(text: str, member: str) -> str:
    """Locate a member in an already validated object using the stdlib decoder."""
    position = _skip(text, 0) + 1
    while text[position := _skip(text, position)] != "}":
        key, position = _DECODER.raw_decode(text, position)
        position = _skip(text, position) + 1  # validated colon
        start = _skip(text, position)
        _, position = _DECODER.raw_decode(text, start)
        if key == member:
            return text[start:position]
        position = _skip(text, position)
        if text[position] == ",":
            position += 1
    raise ValueError("missing member")


def _invoice_fragments(text: str) -> list[str]:
    """Slice already validated array entries; never serialize parsed amounts."""
    result: list[str] = []
    position = _skip(text, 0) + 1
    while text[position := _skip(text, position)] != "]":
        start = position
        _, position = _DECODER.raw_decode(text, start)
        fragment = text[start:position]
        if len(fragment.encode("utf-8")) > MAX_INVOICE_BYTES:
            raise ValueError("invoice too large")
        result.append(fragment)
        position = _skip(text, position)
        if text[position] == ",":
            position += 1
    return result


def _validate_invoice_fields(invoice: dict[str, JsonValue], date_from: str, date_to: str) -> None:
    transaction_date = calendar_date(invoice.get("TxnDate"))
    if not date_from <= transaction_date <= date_to:
        raise ValueError("invoice outside requested dates")
    if "DueDate" in invoice:
        calendar_date(invoice["DueDate"])
    for name in ("TotalAmt", "Balance"):
        if name in invoice and (type(invoice[name]) not in (int, Decimal)):
            raise ValueError("invalid amount")
    for name in ("CustomerRef", "CurrencyRef"):
        if name in invoice:
            reference = _object(invoice[name])
            _identity(reference.get("value"))
            if "name" in reference and not isinstance(reference["name"], str):
                raise ValueError("invalid reference name")


def _record(invoice: dict[str, JsonValue], raw: str) -> InvoiceRecord:
    result: InvoiceRecord = {"native_id": _identity(invoice.get("Id")), "raw_json": raw}
    if "SyncToken" in invoice:
        revision = _identity(invoice["SyncToken"])
        if not re.fullmatch(r"[0-9]+", revision):
            raise ValueError("invalid revision")
        result["source_revision"] = revision
    if "MetaData" in invoice:
        metadata = _object(invoice["MetaData"])
        if "LastUpdatedTime" in metadata:
            timestamp = _identity(metadata["LastUpdatedTime"])
            if datetime.fromisoformat(timestamp).tzinfo is None:
                raise ValueError("timestamp must include offset")
            result["source_updated_at"] = timestamp
    return result


def _page_metadata(query: dict[str, JsonValue], count: int, start: int, limit: int) -> InvoicePage:
    position = _integer(query.get("startPosition", start), 1)
    maximum = _integer(query.get("maxResults", 0))
    if position != start or maximum != count or count > limit:
        raise ValueError("inconsistent page")
    if count and "startPosition" not in query:
        raise ValueError("missing page position")
    result: InvoicePage = {
        "invoices": [],
        "start_position": position,
        "max_results": maximum,
        "count": count,
    }
    if "totalCount" in query:
        result["total_count"] = _integer(query["totalCount"])
    return result


def parse_invoice_page(
    response: httpx.Response,
    *,
    secrets: tuple[str, ...],
    start: int,
    limit: int,
    date_from: str,
    date_to: str,
) -> InvoicePage:
    try:
        text, document = _document(response, secrets)
        query = _object(document.get("QueryResponse"))
        if query.keys() - {"Invoice", "startPosition", "maxResults", "totalCount"}:
            raise ValueError("unexpected query response")
        if "Fault" in document:
            raise ValueError("provider fault")
        invoices = query.get("Invoice", [])
        if not isinstance(invoices, list):
            raise ValueError("expected invoice array")
        result = _page_metadata(query, len(invoices), start, limit)
        fragments = (
            _invoice_fragments(_member_text(_member_text(text, "QueryResponse"), "Invoice"))
            if invoices
            else []
        )
        identities: set[str] = set()
        for value, raw in zip(invoices, fragments, strict=True):
            invoice = _object(value)
            _validate_invoice_fields(invoice, date_from, date_to)
            record = _record(invoice, raw)
            if record["native_id"] in identities:
                raise ValueError("duplicate invoice identity")
            identities.add(record["native_id"])
            result["invoices"].append(record)
        return result
    except (ValueError, TypeError, OverflowError, RecursionError, DecimalException):
        raise invalid_response() from None


def parse_company_info(
    response: httpx.Response, *, secrets: tuple[str, ...], realm_id: str
) -> CompanyInfo:
    try:
        _, document = _document(response, secrets)
        company = _object(document.get("CompanyInfo"))
        if "Fault" in document:
            raise ValueError("provider fault")
        result: CompanyInfo = {"realm_id": realm_id, "company_id": _identity(company.get("Id"))}
        if "CompanyName" in company:
            if not isinstance(company["CompanyName"], str) or not company["CompanyName"].strip():
                raise ValueError("invalid company name")
            result["company_name"] = cast(str, company["CompanyName"])
        return result
    except (ValueError, TypeError, OverflowError, RecursionError, DecimalException):
        raise invalid_response() from None
