"""Finite bounded QuickBooks Online accounting reads."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AsyncExitStack
from functools import partial

import httpx

from stackos_connectors import ConnectorRequest, ConnectorResult, ValidationIssue
from stackos_connectors.redaction import auth_secret_values, redact_secret_values

from .entities import ENTITIES, Entity
from .integration import QuickBooksIntegration
from .response import (
    CompanyInfo,
    InvoicePage,
    RecordsPage,
    calendar_date,
    invalid_response,
    parse_company_info,
    parse_invoice_page,
    parse_records,
)


class QuickBooksActionConnector:
    key = "quickbooks-online"

    def validate(self, request: ConnectorRequest) -> list[ValidationIssue]:
        if request.operation == "records.list":
            if ENTITIES[request.input_json["entity"]].kind != "transaction":
                return []
        elif request.operation != "invoices.list":
            return []
        try:
            first = calendar_date(request.input_json["txn_date_from"])
            last = calendar_date(request.input_json["txn_date_to"])
            if first > last:
                raise ValueError("reversed dates")
        except ValueError:
            return [
                ValidationIssue(path="$.txn_date_from", message="Dates must be valid and ordered")
            ]
        return []

    def estimate_cost_cents(self, request: ConnectorRequest) -> int:
        return 0

    async def execute(self, request: ConnectorRequest) -> ConnectorResult:
        assert request.auth is not None
        config = request.auth.config
        secrets = auth_secret_values(request.auth)
        realm = config["realm_id"]
        path, params, parser = _read_parameters(request, secrets)
        async with AsyncExitStack() as stack:
            http = request.options.http
            if http is None:
                http = await stack.enter_async_context(
                    httpx.AsyncClient(timeout=30.0, follow_redirects=False)
                )
            integration = QuickBooksIntegration(
                payload=b"",
                http=http,
                timeout=request.options.timeout,
                rate_limiter=request.options.rate_limiter,
            )
            result = await integration.read(
                op=request.operation,
                environment=config["environment"],
                realm_id=realm,
                token=request.auth.fields["access_token"],
                path=path,
                params=params,
                parser=parser,
            )
        if redact_secret_values(result.data, secrets) != result.data:
            raise invalid_response()
        return ConnectorResult(
            output_json=result.data,
            metadata_json={"vendor": self.key, "operation": request.operation},
        )


def _read_parameters(
    request: ConnectorRequest, secrets: tuple[str, ...]
) -> tuple[
    str, dict[str, str] | None, Callable[[httpx.Response], CompanyInfo | InvoicePage | RecordsPage]
]:
    assert request.auth is not None
    realm = request.auth.config["realm_id"]
    if request.operation == "company-info.get":
        return (
            f"companyinfo/{realm}",
            None,
            partial(parse_company_info, secrets=secrets, realm_id=realm),
        )
    data = request.input_json
    if request.operation == "records.list":
        return _entity_read_parameters(request, secrets)
    start, limit = _pagination(data["start_position"], data["max_results"])
    parser = partial(
        parse_invoice_page,
        secrets=secrets,
        start=start,
        limit=limit,
        date_from=data["txn_date_from"],
        date_to=data["txn_date_to"],
    )
    query = _invoice_query(data["txn_date_from"], data["txn_date_to"], start, limit)
    return "query", {"query": query}, parser


def _invoice_query(date_from: str, date_to: str, start: int, limit: int) -> str:
    return _entity_query(ENTITIES["Invoice"], start, limit, date_from, date_to)


def _entity_read_parameters(
    request: ConnectorRequest,
    secrets: tuple[str, ...],
) -> tuple[str, dict[str, str] | None, Callable[[httpx.Response], RecordsPage]]:
    assert request.auth is not None
    data = request.input_json
    entity = ENTITIES[data["entity"]]
    parser = partial(
        parse_records,
        secrets=secrets,
        entity=entity,
        date_from=data.get("txn_date_from"),
        date_to=data.get("txn_date_to"),
    )
    if entity.kind == "singleton":
        path = (
            f"companyinfo/{request.auth.config['realm_id']}"
            if entity.name == "CompanyInfo"
            else "preferences"
        )
        return path, None, parser
    start, limit = _pagination(data["start_position"], data["max_results"])
    parser = partial(parser, start=start, limit=limit)
    query = _entity_query(
        entity,
        start,
        limit,
        data.get("txn_date_from"),
        data.get("txn_date_to"),
    )
    return "query", {"query": query}, parser


def _pagination(start: int | float, limit: int | float) -> tuple[int, int]:
    """Canonicalize schema-validated integral JSON numbers before SQL and parsing."""
    return int(start), int(limit)


def _entity_query(
    entity: Entity,
    start: int,
    limit: int,
    date_from: str | None,
    date_to: str | None,
) -> str:
    predicate = ""
    if entity.kind == "transaction":
        predicate = f" where TxnDate >= '{date_from}' and TxnDate <= '{date_to}'"
    elif entity.include_inactive:
        predicate = " where Active IN (true, false)"
    return f"select * from {entity.query_name}{predicate} STARTPOSITION {start} MAXRESULTS {limit}"
