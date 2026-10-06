import json

import httpx
import pytest

from stackos_connectors import CallOptions, ConnectorError, ValidationError, get_default_client

from .conftest import INPUT, INVOICE, auth, page


def test_catalog_has_only_two_reads():
    described = get_default_client().describe("quickbooks-online")
    assert {a["key"] for a in described["actions"]} == {
        "quickbooks-online.company-info.get",
        "quickbooks-online.invoices.list",
    }
    assert all(a["risk_level"] == "read" for a in described["actions"])


async def test_exact_invoice_fragments_and_bounded_query(execute):
    output, requests = await execute(page(INVOICE))
    record = output["invoices"][0]
    assert record == {
        "native_id": "17",
        "source_revision": "2",
        "source_updated_at": "2026-02-01T13:01:02-08:00",
        "raw_json": INVOICE,
    }
    assert output["count"] == output["max_results"] == 1
    assert output["start_position"] == 1
    request = requests[0]
    assert request.method == "GET"
    assert str(request.url).startswith(
        "https://sandbox-quickbooks.api.intuit.com/v3/company/123456789/query?"
    )
    # SDK pagination keywords; omit ordering without Invoice sort support evidence.
    assert request.url.params["query"] == (
        "select * from Invoice where TxnDate >= '2026-01-01' and TxnDate <= '2026-01-31' "
        "STARTPOSITION 1 MAXRESULTS 2"
    )
    assert request.headers["authorization"] == "Bearer fixture-access-canary"
    assert request.extensions["timeout"]["read"] == 0.5


async def test_company_id_is_not_realm_id(execute):
    output, requests = await execute(
        '{"CompanyInfo":{"Id":"1","CompanyName":"Example"}}',
        action="company-info.get",
        data={},
        credential=auth(environment="production"),
    )
    assert output == {"realm_id": "123456789", "company_id": "1", "company_name": "Example"}
    assert (
        str(requests[0].url)
        == "https://quickbooks.api.intuit.com/v3/company/123456789/companyinfo/123456789"
    )


@pytest.mark.parametrize(
    "body", ['{"QueryResponse":{}}', '{"QueryResponse":{"Invoice":[],"maxResults":0}}']
)
async def test_empty_page(execute, body):
    output, _ = await execute(body, data={**INPUT, "start_position": 3})
    assert output == {"invoices": [], "start_position": 3, "max_results": 0, "count": 0}


async def test_zero_missing_currency_and_internal_whitespace_preserved(execute):
    invoice = INVOICE.replace("12345678901234567890.123456789", "0.000").replace("-0.0100", "0")
    output, _ = await execute(page(invoice))
    assert output["invoices"][0]["raw_json"] == invoice
    assert "CurrencyRef" not in output["invoices"][0]["raw_json"]


@pytest.mark.parametrize(
    "data",
    [
        {**INPUT, "txn_date_from": "2026-02-30"},
        {**INPUT, "txn_date_to": "2025-12-31"},
        {**INPUT, "txn_date_from": "2026-1-1"},
        {**INPUT, "max_results": 101},
        {**INPUT, "max_results": True},
        {**INPUT, "start_position": 0},
        {**INPUT, "query": "select * from Customer"},
        {**INPUT, "url": "https://example.com"},
    ],
)
async def test_invalid_query_inputs_never_call_provider(data):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("HTTP called"))
    ) as http:
        with pytest.raises(ValidationError):
            await get_default_client().execute(
                "quickbooks-online",
                "quickbooks-online.invoices.list",
                data,
                auth(),
                options=CallOptions(http=http),
            )


@pytest.mark.parametrize(
    "config",
    [
        {"realm_id": "../1"},
        {"realm_id": "1/2"},
        {"realm_id": ""},
        {"environment": "test"},
        {"base_url": "https://example.com"},
        {"realm_id": 123},
    ],
)
async def test_invalid_company_config_never_calls_provider(config):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("HTTP called"))
    ) as http:
        with pytest.raises(ValidationError):
            await get_default_client().execute(
                "quickbooks-online",
                "quickbooks-online.company-info.get",
                {},
                auth(**config),
                options=CallOptions(http=http),
            )


@pytest.mark.parametrize(
    "body",
    [
        "not-json fixture-access-canary",
        '{"QueryResponse":{},"QueryResponse":{}}',
        page(INVOICE.replace('"Id":"17"', '"Id":"17","Id":"18"')),
        page(INVOICE.replace('"value":"4"', '"value":"4","value":"5"')),
        page(INVOICE, INVOICE),
        page(INVOICE, position=2),
        page(INVOICE).replace('"maxResults": 1', '"maxResults": 2'),
        page(INVOICE).replace('"startPosition": 1', '"startPosition": true'),
        page(INVOICE, totalCount=-1),
        page(INVOICE.replace('"Id":"17"', '"Id":17')),
        page(INVOICE.replace('"SyncToken":"2"', '"SyncToken":null')),
        page(INVOICE.replace("2026-01-31", "2026-02-01")),
        page(INVOICE.replace("-0.0100", "NaN")),
        page(INVOICE.replace('"CustomerRef":', '"CurrencyRef":{"value":null},"CustomerRef":')),
        '{"QueryResponse":{"Invoice":{}}}',
        '{"Fault":{"Error":[]}}',
    ],
)
async def test_malformed_or_inconsistent_evidence_fails_safely(execute, body):
    with pytest.raises(ConnectorError) as caught:
        await execute(body)
    assert caught.value.provider_error["reason_code"] == "invalid_response"
    assert "fixture-access-canary" not in str(caught.value.__dict__)


@pytest.mark.parametrize(
    "echo", ["fixture-access-canary", "fixture-access-canary".replace("f", "\\u0066", 1)]
)
async def test_credential_echo_rejected_before_registry_can_rewrite_raw(execute, echo):
    with pytest.raises(ConnectorError) as caught:
        await execute(page(INVOICE.replace("Example", echo)))
    assert caught.value.provider_error == {"reason_code": "invalid_response"}


@pytest.mark.parametrize(
    "status,calls,reason",
    [(401, 1, "authentication_failed"), (429, 4, "rate_limited"), (503, 4, "provider_unavailable")],
)
async def test_safe_status_errors_and_bounded_read_retry(monkeypatch, status, calls, reason):
    requests = []

    async def no_sleep(delay):
        pass

    monkeypatch.setattr("stackos_connectors.shared.base.asyncio.sleep", no_sleep)

    def handler(request):
        requests.append(request)
        return httpx.Response(
            status,
            content=b"private body and fixture-access-canary",
            headers={"Retry-After": "0.001"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ConnectorError) as caught:
            await get_default_client().execute(
                "quickbooks-online",
                "quickbooks-online.invoices.list",
                INPUT,
                auth(),
                options=CallOptions(http=http),
            )
        assert not http.is_closed
    assert len(requests) == calls
    assert caught.value.provider_status_code == status
    assert caught.value.provider_error["reason_code"] == reason
    assert "private body" not in json.dumps(caught.value.__dict__)
    assert "fixture-access-canary" not in json.dumps(caught.value.__dict__)


async def test_redirects_disabled_even_for_injected_client():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://evil.example/"}, content=b"{}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as http:
        with pytest.raises(ConnectorError):
            await get_default_client().execute(
                "quickbooks-online",
                "quickbooks-online.company-info.get",
                {},
                auth(),
                options=CallOptions(http=http),
            )
    assert len(requests) == 1


async def test_optional_source_metadata_and_provider_total_are_preserved(execute):
    raw = '{"Id":"18","TxnDate":"2026-01-01","TotalAmt":1e-20,"Balance":-10}'
    output, _ = await execute(page(raw, totalCount=0))
    assert output["invoices"] == [{"native_id": "18", "raw_json": raw}]
    assert output["total_count"] == 0  # Provider metadata never drives completion.


@pytest.mark.parametrize(
    "body",
    [
        page(INVOICE.replace("Example", "x" * 16384)),
        " " * (2 * 1024 * 1024) + '{"QueryResponse":{}}',
        page(INVOICE).replace('"startPosition": 1, ', ""),
        page(INVOICE).replace(', "maxResults": 1', ""),
        page(INVOICE).replace(
            '"LastUpdatedTime":"2026-02-01T13:01:02-08:00"', '"LastUpdatedTime":"2026-02-01"'
        ),
        page(INVOICE).replace('"Balance":-0.0100', '"Balance":true'),
        page(INVOICE).replace('"TotalAmt":12345678901234567890.123456789', '"TotalAmt":"1.2"'),
        page(INVOICE).replace('"TxnDate":"2026-01-31"', '"TxnDate":"2026-02-30"'),
        b"\xff",
    ],
)
async def test_record_response_and_metadata_bounds(execute, body):
    with pytest.raises(ConnectorError) as caught:
        await execute(body)
    assert caught.value.provider_error == {"reason_code": "invalid_response"}


@pytest.mark.parametrize(
    "body",
    [
        '{"CompanyInfo":{"Id":1}}',
        '{"CompanyInfo":{"Id":"1","CompanyName":null}}',
        '{"CompanyInfo":{"Id":"1","Id":"2"}}',
        '{"CompanyInfo":[]}',
        '{"CompanyInfo":{"Id":"1","CompanyName":"fixture-access-canary"}}',
    ],
)
async def test_malformed_company_response(execute, body):
    with pytest.raises(ConnectorError) as caught:
        await execute(body, action="company-info.get", data={})
    assert caught.value.provider_error == {"reason_code": "invalid_response"}


async def test_shared_transport_http_error_is_safe_and_caller_owned(monkeypatch):
    async def no_sleep(delay):
        pass

    monkeypatch.setattr("stackos_connectors.shared.base.asyncio.sleep", no_sleep)

    def handler(request):
        raise httpx.ConnectError("fixture-access-canary arbitrary socket detail")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ConnectorError) as caught:
            await get_default_client().execute(
                "quickbooks-online",
                "quickbooks-online.company-info.get",
                {},
                auth(),
                options=CallOptions(http=http),
            )
        assert not http.is_closed
    assert caught.value.provider_error == {"reason_code": "network_error"}
    assert "canary" not in str(caught.value.__dict__)


async def test_output_keys_cannot_be_silently_redacted(execute):
    from stackos_connectors import ConnectorAuth

    credential = ConnectorAuth(
        "oauth2_token",
        {"access_token": "raw_json"},
        config={"environment": "sandbox", "realm_id": "123456789"},
    )
    with pytest.raises(ConnectorError) as caught:
        await execute(page(INVOICE), credential=credential)
    assert caught.value.provider_error == {"reason_code": "invalid_response"}


@pytest.mark.parametrize(
    "body",
    [
        '{"QueryResponse":{"Customer":[]}}',
        page(INVOICE.replace('"Id":"17"', '"Id":" 17 "')),
        page(INVOICE.replace('"Id":"17"', '"Id":"\\ud800"')),
    ],
)
async def test_unexpected_entities_and_invalid_identity(execute, body):
    with pytest.raises(ConnectorError):
        await execute(body)


async def test_record_exactly_16_kib_is_accepted(execute):
    template = '{"Id":"19","TxnDate":"2026-01-31","PrivateNote":""}'
    raw = template.replace(
        '"PrivateNote":""', '"PrivateNote":"' + "x" * (16384 - len(template)) + '"'
    )
    assert len(raw.encode()) == 16384
    output, _ = await execute(page(raw))
    assert output["invoices"][0]["raw_json"] == raw


@pytest.mark.parametrize("valid", [True, False])
async def test_connector_closes_only_its_owned_client(monkeypatch, valid):
    body = '{"CompanyInfo":{"Id":"1"}}' if valid else "{}"
    owned = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))
    )
    monkeypatch.setattr(
        "stackos_connectors.connectors.quickbooks_online.actions.httpx.AsyncClient",
        lambda **kwargs: owned,
    )
    call = get_default_client().execute(
        "quickbooks-online", "quickbooks-online.company-info.get", {}, auth()
    )
    if valid:
        await call
    else:
        with pytest.raises(ConnectorError):
            await call
    assert owned.is_closed
