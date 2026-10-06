"""The two bounded QuickBooks Online read actions."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AsyncExitStack
from functools import partial

import httpx

from stackos_connectors import ConnectorRequest, ConnectorResult, ValidationIssue
from stackos_connectors.redaction import auth_secret_values, redact_secret_values

from .integration import QuickBooksIntegration
from .response import (
    CompanyInfo,
    InvoicePage,
    calendar_date,
    invalid_response,
    parse_company_info,
    parse_invoice_page,
)


class QuickBooksActionConnector:
    key = "quickbooks-online"

    def validate(self, request: ConnectorRequest) -> list[ValidationIssue]:
        if request.operation != "invoices.list":
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
) -> tuple[str, dict[str, str] | None, Callable[[httpx.Response], CompanyInfo | InvoicePage]]:
    assert request.auth is not None
    realm = request.auth.config["realm_id"]
    if request.operation == "company-info.get":
        return (
            f"companyinfo/{realm}",
            None,
            partial(parse_company_info, secrets=secrets, realm_id=realm),
        )
    data = request.input_json
    parser = partial(
        parse_invoice_page,
        secrets=secrets,
        start=data["start_position"],
        limit=data["max_results"],
        date_from=data["txn_date_from"],
        date_to=data["txn_date_to"],
    )
    query = _invoice_query(
        data["txn_date_from"], data["txn_date_to"], data["start_position"], data["max_results"]
    )
    return "query", {"query": query}, parser


def _invoice_query(date_from: str, date_to: str, start: int, limit: int) -> str:
    return (
        f"select * from Invoice where TxnDate >= '{date_from}' and TxnDate <= '{date_to}' "
        f"STARTPOSITION {start} MAXRESULTS {limit}"
    )
