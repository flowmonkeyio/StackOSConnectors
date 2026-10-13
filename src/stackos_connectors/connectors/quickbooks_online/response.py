"""Validate QBO envelopes while retaining exact native JSON source fragments."""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal, DecimalException
from typing import NotRequired, TypedDict, cast

import httpx

from stackos_connectors.errors import ConnectorError
from stackos_connectors.redaction import redact_secret_values

from .entities import ENTITIES, Entity

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_RECORD_BYTES = 16 * 1024
MAX_INVOICE_BYTES = MAX_RECORD_BYTES  # Existing private import compatibility.
_WS = re.compile(r"[ \t\n\r]*")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_SOURCE_CALENDAR = re.compile(
    r"([0-9]{4}-[0-9]{2}-[0-9]{2})"
    r"(?:T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?)?"
    r"(?:Z|[+-][0-9]{2}:[0-9]{2})?"
)
type JsonValue = dict[str, JsonValue] | list[JsonValue] | str | int | Decimal | bool | None


class SourceRecord(TypedDict):
    native_id: str
    raw_json: str
    source_revision: NotRequired[str]
    source_updated_at: NotRequired[str]


class PageMetadata(TypedDict):
    start_position: int
    max_results: int
    count: int
    total_count: NotRequired[int]


class InvoicePage(PageMetadata):
    invoices: list[SourceRecord]


class RecordsPage(TypedDict):
    entity: str
    records: list[SourceRecord]
    count: int
    start_position: NotRequired[int]
    max_results: NotRequired[int]
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


def _source_text(value: JsonValue) -> str:
    """Keep bounded untrusted source text exact; the consumer owns semantics."""
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError("invalid source text")
    return value


def _supplied_calendar_date(value: JsonValue) -> str:
    """Select the printed day from source Date/DateTime, without instant conversion."""
    match = _SOURCE_CALENDAR.fullmatch(_source_text(value))
    if match is None:
        raise ValueError("unselectable source date")
    return match[1]


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


def _record_fragments(text: str) -> list[str]:
    """Slice already validated array entries; never serialize parsed amounts."""
    result: list[str] = []
    position = _skip(text, 0) + 1
    while text[position := _skip(text, position)] != "]":
        start = position
        _, position = _DECODER.raw_decode(text, start)
        fragment = text[start:position]
        result.append(fragment)
        position = _skip(text, position)
        if text[position] == ",":
            position += 1
    return result


def _record(value: dict[str, JsonValue], raw: str) -> SourceRecord:
    if len(raw.encode("utf-8")) > MAX_RECORD_BYTES:
        raise ValueError("record too large")
    result: SourceRecord = {"native_id": _identity(value.get("Id")), "raw_json": raw}
    if "SyncToken" in value:
        revision = _identity(value["SyncToken"])
        if not re.fullmatch(r"[0-9]+", revision):
            raise ValueError("invalid revision")
        result["source_revision"] = revision
    if "MetaData" in value:
        metadata = _object(value["MetaData"])
        if "LastUpdatedTime" in metadata:
            result["source_updated_at"] = _source_text(metadata["LastUpdatedTime"])
    return result


def _page_metadata(query: dict[str, JsonValue], count: int, start: int, limit: int) -> PageMetadata:
    position = _integer(query.get("startPosition", start), 1)
    maximum = _integer(query.get("maxResults", 0))
    if position != start or maximum != count or count > limit:
        raise ValueError("inconsistent page")
    if count and "startPosition" not in query:
        raise ValueError("missing page position")
    result: PageMetadata = {
        "start_position": position,
        "max_results": maximum,
        "count": count,
    }
    if "totalCount" in query:
        result["total_count"] = _integer(query["totalCount"])
    return result


def _validate_native_fields(value: dict[str, JsonValue], entity: Entity) -> None:
    for name in entity.strings:
        if name in value and not isinstance(value[name], str):
            raise ValueError("invalid string")
    for name in entity.numbers:
        if name in value and type(value[name]) not in (int, Decimal):
            raise ValueError("invalid number")
    for name in entity.booleans:
        if name in value and type(value[name]) is not bool:
            raise ValueError("invalid boolean")
    for name in entity.dates:
        if name in value:
            _source_text(value[name])
    for name in entity.references:
        if name in value:
            reference = _object(value[name])
            _identity(reference.get("value"))
            if "name" in reference and not isinstance(reference["name"], str):
                raise ValueError("invalid reference name")
    if entity.name == "Preferences" and "CurrencyPrefs" in value:
        preferences = _object(value["CurrencyPrefs"])
        if (
            "MultiCurrencyEnabled" in preferences
            and type(preferences["MultiCurrencyEnabled"]) is not bool
        ):
            raise ValueError("invalid currency preference")
        if "HomeCurrency" in preferences:
            _identity(_object(preferences["HomeCurrency"]).get("value"))


def _validate_record_fields(
    value: dict[str, JsonValue],
    entity: Entity,
    date_from: str | None,
    date_to: str | None,
) -> None:
    if entity.kind == "transaction":
        assert date_from is not None and date_to is not None
        transaction_date = _supplied_calendar_date(value.get("TxnDate"))
        if not date_from <= transaction_date <= date_to:
            raise ValueError("transaction outside requested dates")
    _validate_native_fields(value, entity)


def _query_records(
    text: str,
    document: dict[str, JsonValue],
    entity: Entity,
    start: int,
    limit: int,
    date_from: str | None,
    date_to: str | None,
) -> RecordsPage:
    query = _object(document.get("QueryResponse"))
    if query.keys() - {*entity.response_keys, "startPosition", "maxResults", "totalCount"}:
        raise ValueError("unexpected query response")
    present = [key for key in entity.response_keys if key in query]
    if len(present) > 1:
        raise ValueError("ambiguous entity response")
    records = query[present[0]] if present else []
    if not isinstance(records, list):
        raise ValueError("expected record array")
    metadata = _page_metadata(query, len(records), start, limit)
    fragments = (
        _record_fragments(_member_text(_member_text(text, "QueryResponse"), present[0]))
        if records
        else []
    )
    result: RecordsPage = {"entity": entity.name, "records": [], **metadata}
    identities: set[str] = set()
    for value, raw in zip(records, fragments, strict=True):
        native = _object(value)
        _validate_record_fields(native, entity, date_from, date_to)
        record = _record(native, raw)
        if record["native_id"] in identities:
            raise ValueError("duplicate record identity")
        identities.add(record["native_id"])
        result["records"].append(record)
    return result


def parse_records(
    response: httpx.Response,
    *,
    secrets: tuple[str, ...],
    entity: Entity,
    start: int | None = None,
    limit: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> RecordsPage:
    try:
        text, document = _document(response, secrets)
        if "Fault" in document:
            raise ValueError("provider fault")
        if entity.kind == "singleton":
            if document.keys() - {entity.name, "time"}:
                raise ValueError("unexpected singleton response")
            native = _object(document.get(entity.name))
            _validate_native_fields(native, entity)
            record = _record(native, _member_text(text, entity.name))
            return {"entity": entity.name, "records": [record], "count": 1}
        assert start is not None and limit is not None
        return _query_records(text, document, entity, start, limit, date_from, date_to)
    except (ValueError, TypeError, OverflowError, RecursionError, DecimalException):
        raise invalid_response() from None


def parse_invoice_page(
    response: httpx.Response,
    *,
    secrets: tuple[str, ...],
    start: int,
    limit: int,
    date_from: str,
    date_to: str,
) -> InvoicePage:
    page = parse_records(
        response,
        secrets=secrets,
        entity=ENTITIES["Invoice"],
        start=start,
        limit=limit,
        date_from=date_from,
        date_to=date_to,
    )
    result: InvoicePage = {
        "invoices": page["records"],
        "start_position": page["start_position"],
        "max_results": page["max_results"],
        "count": page["count"],
    }
    if "total_count" in page:
        result["total_count"] = page["total_count"]
    return result


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
