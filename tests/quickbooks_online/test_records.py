"""Finite entity contracts checked through the public connector boundary."""

import json

import httpx
import pytest

from stackos_connectors import CallOptions, ConnectorError, ValidationError, get_default_client

from .conftest import auth, entity_input, entity_page

TRANSACTIONS = (
    "Invoice",
    "Payment",
    "SalesReceipt",
    "RefundReceipt",
    "CreditMemo",
    "Bill",
    "BillPayment",
    "Purchase",
    "VendorCredit",
    "Deposit",
    "Transfer",
    "JournalEntry",
    "Estimate",
    "PurchaseOrder",
    "CreditCardPayment",
    "InventoryAdjustment",
)
ACTIVE_REFERENCES = (
    "Account",
    "Customer",
    "Vendor",
    "Employee",
    "Item",
    "Class",
    "Department",
    "PaymentMethod",
    "Term",
    "TaxCode",
    "TaxRate",
)
SINGLETONS = {"CompanyInfo": "companyinfo/123456789", "Preferences": "preferences"}
ENTITIES = (*TRANSACTIONS, *ACTIVE_REFERENCES, "TaxAgency", *SINGLETONS)
RAW = (
    '{ "Id":"42", "SyncToken":"3", "TxnDate":"2026-01-15",'
    ' "TotalAmt":9007199254740993.0100,"Line":[{"Amount":-1e-20}],'
    ' "MetaData":{"LastUpdatedTime":"2026-01-20T10:20:30Z"}}'
)


@pytest.mark.parametrize("entity", ENTITIES)
async def test_finite_entity_reads_preserve_native_evidence(execute, entity):
    singleton = entity in SINGLETONS
    raw = RAW if entity in TRANSACTIONS else RAW.replace(' "TxnDate":"2026-01-15",', "")
    data = entity_input(entity, transaction=entity in TRANSACTIONS, singleton=singleton)
    body = '{"' + entity + '":' + raw + "}" if singleton else entity_page(entity, raw)
    output, requests = await execute(body, action="records.list", data=data)
    assert output["entity"] == entity
    assert output["records"] == [
        {
            "native_id": "42",
            "raw_json": raw,
            "source_revision": "3",
            "source_updated_at": "2026-01-20T10:20:30Z",
        }
    ]
    assert output["count"] == 1
    request = requests[0]
    assert request.method == "GET"
    if singleton:
        assert request.url.path == "/v3/company/123456789/" + SINGLETONS[entity]
        assert not request.url.query
        assert set(output) == {"entity", "records", "count"}
    else:
        identifier = "creditcardpayment" if entity == "CreditCardPayment" else entity
        predicate = ""
        if entity in TRANSACTIONS:
            predicate = " where TxnDate >= '2026-01-01' and TxnDate <= '2026-01-31'"
        elif entity in ACTIVE_REFERENCES:
            predicate = " where Active IN (true, false)"
        assert request.url.params["query"] == (
            f"select * from {identifier}{predicate} STARTPOSITION 1 MAXRESULTS 100"
        )
        assert output["start_position"] == output["max_results"] == 1


def test_public_schema_enumerates_exactly_the_supported_catalog():
    from jsonschema import Draft202012Validator

    described = get_default_client().describe("quickbooks-online")
    action = next(a for a in described["actions"] if a["key"].endswith("records.list"))
    schema = action["input_schema"]
    validator = Draft202012Validator(schema)
    for entity in ENTITIES:
        assert validator.is_valid(
            entity_input(entity, transaction=entity in TRANSACTIONS, singleton=entity in SINGLETONS)
        )
    assert len(ENTITIES) == 30
    assert not validator.is_valid({"entity": "Budget", "start_position": 1, "max_results": 100})


@pytest.mark.parametrize("entity", (*TRANSACTIONS, *ACTIVE_REFERENCES, "TaxAgency"))
async def test_empty_query_is_distinct_from_wrong_entity_or_fault(execute, entity):
    data = entity_input(entity, transaction=entity in TRANSACTIONS)
    output, _ = await execute('{"QueryResponse":{}}', action="records.list", data=data)
    assert output == {
        "entity": entity,
        "records": [],
        "count": 0,
        "start_position": 1,
        "max_results": 0,
    }
    for body in (
        '{"QueryResponse":{"Unsupported":[]}}',
        '{"Fault":{"Error":[]},"QueryResponse":{}}',
    ):
        with pytest.raises(ConnectorError):
            await execute(body, action="records.list", data=data)


@pytest.mark.parametrize(
    "data",
    [
        {"entity": "Budget", "start_position": 1, "max_results": 100},
        {"entity": "Bill", "start_position": 1, "max_results": 100},
        {**entity_input("Bill", transaction=True), "txn_date_from": "2026-02-30"},
        {**entity_input("Bill", transaction=True), "txn_date_from": "2026-02-01"},
        {**entity_input("Bill", transaction=True), "max_results": 101},
        {**entity_input("Bill", transaction=True), "max_results": True},
        {**entity_input("Bill", transaction=True), "start_position": 0},
        {**entity_input("Bill", transaction=True), "query": "select * from Bill"},
        {**entity_input("Bill", transaction=True), "entity": "Bill where Id > 0"},
        {**entity_input("Account"), "txn_date_from": "2026-01-01"},
        {"entity": "Preferences", "start_position": 1, "max_results": 1},
        {"entity": "CompanyInfo", "url": "https://evil.example"},
    ],
)
async def test_invalid_entity_scope_never_calls_provider(data):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("HTTP called"))
    ) as http:
        with pytest.raises(ValidationError):
            await get_default_client().execute(
                "quickbooks-online",
                "quickbooks-online.records.list",
                data,
                auth(),
                options=CallOptions(http=http),
            )


@pytest.mark.parametrize("key", ["CreditCardPayment", "CreditCardPaymentTxn"])
async def test_credit_card_payment_has_finite_response_aliases(execute, key):
    output, _ = await execute(
        entity_page(key, RAW),
        action="records.list",
        data=entity_input("CreditCardPayment", transaction=True),
    )
    assert output["entity"] == "CreditCardPayment"
    assert output["records"][0]["raw_json"] == RAW


@pytest.mark.parametrize(
    "body",
    [
        '{"QueryResponse":{"CreditCardPayment":[],"CreditCardPaymentTxn":[]}}',
        '{"QueryResponse":{"creditcardpayment":[]}}',
    ],
)
async def test_ambiguous_or_unknown_alias_rejected(execute, body):
    with pytest.raises(ConnectorError):
        await execute(
            body, action="records.list", data=entity_input("CreditCardPayment", transaction=True)
        )


@pytest.mark.parametrize(
    "raw",
    [
        '{"Id":"42"}',
        '{"Id":"42","Active":false}',
        '{"Id":"42","Name":"","CurrentBalance":-0.0000000001}',
    ],
)
async def test_reference_optional_and_inactive_fields_preserved(execute, raw):
    output, _ = await execute(
        entity_page("Account", raw), action="records.list", data=entity_input("Account")
    )
    assert output["records"] == [{"native_id": "42", "raw_json": raw}]


@pytest.mark.parametrize(
    "body",
    [
        entity_page("Bill", RAW, RAW),
        entity_page("Bill", RAW.replace('"Id":"42"', '"Id":"42","Id":"43"')),
        entity_page("Bill", RAW.replace('"Id":"42"', '"Id":42')),
        entity_page("Bill", RAW.replace('"SyncToken":"3"', '"SyncToken":null')),
        entity_page("Bill", RAW.replace("2026-01-15", "2026-02-01")),
        entity_page("Bill", RAW.replace("9007199254740993.0100", '"1.5"')),
        entity_page("Bill", RAW.replace("9007199254740993.0100", "NaN")),
        entity_page("Bill", RAW.replace("2026-01-20T10:20:30Z", "2026-01-20")),
        entity_page("Bill", RAW, position=2),
        entity_page("Bill", RAW).replace('"maxResults": 1', '"maxResults": 2'),
        entity_page("Bill", RAW.replace('"Id":"42"', '"Id":"fixture-access-canary"')),
        entity_page("Bill", '{"Id":"42","TxnDate":"2026-01-15","Note":"' + "x" * 16384 + '"}'),
        " " * (2 * 1024 * 1024) + '{"QueryResponse":{}}',
    ],
)
async def test_malformed_generic_evidence_is_not_published_or_leaked(execute, body):
    with pytest.raises(ConnectorError) as caught:
        await execute(body, action="records.list", data=entity_input("Bill", transaction=True))
    assert caught.value.provider_error == {"reason_code": "invalid_response"}
    assert "fixture-access-canary" not in json.dumps(caught.value.__dict__)


@pytest.mark.parametrize(
    "entity,raw",
    [
        ("Account", '{"Id":"1","Active":"false"}'),
        ("Vendor", '{"Id":"1","CurrencyRef":{"value":null}}'),
        ("Item", '{"Id":"1","UnitPrice":true}'),
        ("Term", '{"Id":"1","DueDays":"30"}'),
        ("TaxRate", '{"Id":"1","RateValue":"1.25"}'),
        ("Preferences", '{"Id":"1","CurrencyPrefs":{"MultiCurrencyEnabled":"yes"}}'),
        ("CompanyInfo", '{"CompanyName":"Missing ID"}'),
    ],
)
async def test_present_wrong_native_types_fail(execute, entity, raw):
    singleton = entity in SINGLETONS
    body = '{"' + entity + '":' + raw + "}" if singleton else entity_page(entity, raw)
    with pytest.raises(ConnectorError):
        await execute(body, action="records.list", data=entity_input(entity, singleton=singleton))


@pytest.mark.parametrize("entity", SINGLETONS)
async def test_singleton_must_return_exactly_one_native_object(execute, entity):
    for body in ("{}", '{"' + entity + '":[]}', '{"' + entity + '":{"Id":"1"},"Fault":{}}'):
        with pytest.raises(ConnectorError):
            await execute(body, action="records.list", data={"entity": entity})


@pytest.mark.parametrize("count", [100, 101])
async def test_actual_page_capacity_never_truncates(execute, count):
    raw_records = [f'{{"Id":"{index}","Active":false}}' for index in range(count)]
    call = execute(
        entity_page("Account", *raw_records), action="records.list", data=entity_input("Account")
    )
    if count > 100:
        with pytest.raises(ConnectorError):
            await call
    else:
        output, _ = await call
        assert output["count"] == 100
        assert [record["raw_json"] for record in output["records"]] == raw_records


@pytest.mark.parametrize(
    "action,entity,transaction",
    [
        ("records.list", "Account", False),
        ("records.list", "Bill", True),
        ("invoices.list", "Invoice", True),
    ],
)
@pytest.mark.parametrize("empty", [False, True])
async def test_integral_pagination_uses_native_integer_query_and_output(
    execute,
    action,
    entity,
    transaction,
    empty,
):
    # JSON Schema integer admits 1.0; provider query syntax and fallback metadata
    # must receive canonical integers regardless of the JSON number spelling.
    data = entity_input(entity, transaction=transaction)
    data.update(start_position=3.0, max_results=100.0)
    if action == "invoices.list":
        data.pop("entity")
    body = '{"QueryResponse":{}}' if empty else entity_page(entity, RAW, position=3)
    output, requests = await execute(body, action=action, data=data)
    assert requests[0].url.params["query"].endswith("STARTPOSITION 3 MAXRESULTS 100")
    assert type(output["start_position"]) is int
    assert type(output["max_results"]) is int
    assert output["start_position"] == 3
    assert output["count"] == (0 if empty else 1)


@pytest.mark.parametrize(
    "action,entity,transaction",
    [
        ("records.list", "Account", False),
        ("records.list", "Bill", True),
        ("invoices.list", "Invoice", True),
    ],
)
@pytest.mark.parametrize(
    "field,value",
    [
        ("start_position", 1.5),
        ("max_results", 1.5),
        ("start_position", True),
        ("max_results", True),
    ],
)
async def test_nonintegral_or_boolean_pagination_never_calls_provider(
    action,
    entity,
    transaction,
    field,
    value,
):
    data = entity_input(entity, transaction=transaction)
    data[field] = value
    if action == "invoices.list":
        data.pop("entity")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("HTTP called"))
    ) as http:
        with pytest.raises(ValidationError):
            await get_default_client().execute(
                "quickbooks-online",
                f"quickbooks-online.{action}",
                data,
                auth(),
                options=CallOptions(http=http),
            )
