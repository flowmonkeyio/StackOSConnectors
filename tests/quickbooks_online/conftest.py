import httpx
import pytest

from stackos_connectors import CallOptions, ConnectorAuth, get_default_client

INVOICE = (
    '{ "Id":"17", "SyncToken":"2", "TxnDate":"2026-01-31",'
    ' "CustomerRef":{"value":"4","name":"Example"},'
    ' "TotalAmt":12345678901234567890.123456789, "Balance":-0.0100,'
    ' "MetaData":{"LastUpdatedTime":"2026-02-01T13:01:02-08:00"}}'
)
INPUT = {
    "txn_date_from": "2026-01-01",
    "txn_date_to": "2026-01-31",
    "start_position": 1,
    "max_results": 2,
}


def auth(**config):
    return ConnectorAuth(
        "oauth2_token",
        {"access_token": "fixture-access-canary"},
        config={"environment": "sandbox", "realm_id": "123456789", **config},
    )


def page(*invoices, position=1, **metadata):
    return entity_page("Invoice", *invoices, position=position, **metadata)


@pytest.fixture
def execute():
    async def call(body, *, action="invoices.list", data=None, credential=None, status=200):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(status, content=body, headers={"Retry-After": "0.001"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await get_default_client().execute(
                "quickbooks-online",
                f"quickbooks-online.{action}",
                INPUT if data is None else data,
                credential or auth(),
                options=CallOptions(http=http, timeout=0.5),
            )
            assert not http.is_closed
        return result.output_json, requests

    return call


def entity_input(entity, *, transaction=False, singleton=False):
    result = {"entity": entity}
    if not singleton:
        result.update(start_position=1, max_results=100)
    if transaction:
        result.update(txn_date_from="2026-01-01", txn_date_to="2026-01-31")
    return result


def entity_page(entity, *records, position=1, **metadata):
    import json

    fields = {"startPosition": position, "maxResults": len(records), **metadata}
    return (
        '{"QueryResponse":{"'
        + entity
        + '":['
        + ",".join(records)
        + "],"
        + json.dumps(fields)[1:]
        + "}"
    )


def temporal_record(*, txn_date='"2026-01-31"', due_date=None, updated_at=None):
    """Literal HTTP record control; arguments are exact JSON member tokens."""
    fields = ['"Id":"42"', '"SyncToken":"3"']
    if txn_date is not None:
        fields.append('"TxnDate":' + txn_date)
    if due_date is not None:
        fields.append('"DueDate":' + due_date)
    fields.extend(['"TotalAmt":9007199254740993.0100', '"Line":[{"Amount":-1e-20}]'])
    if updated_at is not None:
        fields.append('"MetaData":{"LastUpdatedTime":' + updated_at + "}")
    return "{ " + ", ".join(fields) + "}"
