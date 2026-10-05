"""Public package requests, selected auth methods and pre-dispatch rejection."""

import asyncio
import json
from importlib.resources import files
from urllib.parse import parse_qs

import httpx
import pytest

from stackos_connectors import CallOptions, ConnectorAuth, ConnectorClient, ValidationError
from stackos_connectors.catalog import load_registry
from stackos_connectors.connectors.hubspot.integration import HubSpotIntegration
from stackos_connectors.probe import AuthMethodProbeContext

REGISTRY = load_registry(*(f"connectors/{p}/catalog.json" for p in ("hubspot", "linear", "stripe")))
CLIENT = ConnectorClient(registry=REGISTRY)


CASES = [
    (
        "hubspot",
        "hubspot.marketing.campaigns.create",
        {"name": "Launch", "currency_code": "usd", "start_date": "2026-01-01", "status": "planned"},
        "POST",
        "https://api.hubapi.com/marketing/campaigns/2026-03",
        {
            "properties": {
                "hs_name": "Launch",
                "hs_currency_code": "USD",
                "hs_start_date": "2026-01-01",
                "hs_campaign_status": "planned",
            }
        },
    ),
    (
        "hubspot",
        "hubspot.marketing.campaigns.update",
        {"campaign_id": "123", "name": "Updated", "end_date": "2026-01-30"},
        "PATCH",
        "https://api.hubapi.com/marketing/campaigns/2026-03/123",
        {"properties": {"hs_name": "Updated", "hs_end_date": "2026-01-30"}},
    ),
    (
        "hubspot",
        "hubspot.marketing.events.upsert",
        {
            "external_account_key": "account",
            "external_event_key": "event",
            "name": "Launch",
            "organizer": "Team",
            "event_cancelled": False,
            "custom_properties": {"zeta": "z", "alpha": "a"},
        },
        "POST",
        "https://api.hubapi.com/marketing/marketing-events/2026-03/events/upsert",
        {
            "inputs": [
                {
                    "externalAccountId": "account",
                    "externalEventId": "event",
                    "eventName": "Launch",
                    "eventOrganizer": "Team",
                    "eventCancelled": False,
                    "customProperties": [
                        {"name": "alpha", "value": "a"},
                        {"name": "zeta", "value": "z"},
                    ],
                }
            ]
        },
    ),
    (
        "stripe",
        "stripe.customers.update",
        {"customer_id": "cus_native", "name": "Ada", "invoice_settings": {"custom_fields": []}},
        "POST",
        "https://api.stripe.com/v1/customers/cus_native",
        {"name": ["Ada"], "invoice_settings[custom_fields]": [""]},
    ),
    (
        "stripe",
        "stripe.invoices.create",
        {
            "customer_id": "cus_native",
            "currency": "usd",
            "days_until_due": 7,
            "collection_method": "send_invoice",
            "auto_advance": False,
            "metadata": {"external": "occurrence"},
        },
        "POST",
        "https://api.stripe.com/v1/invoices",
        {
            "customer": ["cus_native"],
            "currency": ["usd"],
            "days_until_due": ["7"],
            "collection_method": ["send_invoice"],
            "auto_advance": ["false"],
            "metadata[external]": ["occurrence"],
        },
    ),
    (
        "stripe",
        "stripe.invoices.attach-payment",
        {"invoice_id": "in_native", "payment_record_id": "pyr_native"},
        "POST",
        "https://api.stripe.com/v1/invoices/in_native/attach_payment",
        {"payment_record": ["pyr_native"]},
    ),
    (
        "stripe",
        "stripe.invoices.finalize",
        {"invoice_id": "in_native", "auto_advance": True},
        "POST",
        "https://api.stripe.com/v1/invoices/in_native/finalize",
        {"auto_advance": ["true"]},
    ),
    (
        "stripe",
        "stripe.invoice-items.create",
        {
            "invoice_id": "in_native",
            "customer_id": "cus_native",
            "price_id": "price_native",
            "quantity": 2,
        },
        "POST",
        "https://api.stripe.com/v1/invoiceitems",
        {
            "invoice": ["in_native"],
            "customer": ["cus_native"],
            "pricing[price]": ["price_native"],
            "quantity": ["2"],
        },
    ),
    (
        "stripe",
        "stripe.invoices.list",
        {"customer_id": "cus_native", "created_gte": 10, "starting_after": "in_after", "limit": 3},
        "GET",
        "https://api.stripe.com/v1/invoices?limit=3&starting_after=in_after&customer=cus_native&created%5Bgte%5D=10",
        None,
    ),
    (
        "stripe",
        "stripe.invoices.retrieve",
        {"invoice_id": "in_native"},
        "GET",
        "https://api.stripe.com/v1/invoices/in_native",
        None,
    ),
    (
        "hubspot",
        "hubspot.crm.contacts.search",
        {
            "filterGroups": [
                {
                    "filters": [
                        {"propertyName": "email", "operator": "EQ", "value": "a@example.test"}
                    ]
                }
            ],
            "properties": ["email"],
            "limit": 2,
        },
        "POST",
        "https://api.hubapi.com/crm/objects/2026-03/contacts/search",
        {
            "filterGroups": [
                {
                    "filters": [
                        {"propertyName": "email", "operator": "EQ", "value": "a@example.test"}
                    ]
                }
            ],
            "properties": ["email"],
            "limit": 2,
        },
    ),
    (
        "hubspot",
        "hubspot.crm.contact_company.associate",
        {
            "from_id": "101",
            "to_id": "202",
            "types": [{"associationCategory": "USER_DEFINED", "associationTypeId": 7}],
        },
        "PUT",
        "https://api.hubapi.com/crm/objects/2026-03/contact/101/associations/company/202",
        [{"associationCategory": "USER_DEFINED", "associationTypeId": 7}],
    ),
    (
        "hubspot",
        "hubspot.bulk.exports.status",
        {"job_id": "123"},
        "GET",
        "https://api.hubapi.com/crm/exports/2026-03/export/123",
        None,
    ),
    (
        "hubspot",
        "hubspot.bulk.exports.result",
        {"job_id": "123"},
        "GET",
        "https://api.hubapi.com/crm/exports/2026-03/export/async/tasks/123/status",
        None,
    ),
    (
        "hubspot",
        "hubspot.marketing.segments.memberships.add",
        {"segment_id": "123", "ids": ["11", "22"]},
        "PUT",
        "https://api.hubapi.com/crm/lists/2026-03/123/memberships/add",
        ["11", "22"],
    ),
    (
        "hubspot",
        "hubspot.marketing.contact_preferences.update",
        {
            "email": "a@example.test",
            "subscriptionId": 4,
            "statusState": "SUBSCRIBED",
            "legalBasis": "CONSENT_WITH_NOTICE",
            "legalBasisExplanation": "Recorded consent",
            "channel": "EMAIL",
        },
        "POST",
        "https://api.hubapi.com/communication-preferences/2026-03/statuses/a%40example.test",
        {
            "subscriptionId": 4,
            "statusState": "SUBSCRIBED",
            "legalBasis": "CONSENT_WITH_NOTICE",
            "legalBasisExplanation": "Recorded consent",
            "channel": "EMAIL",
        },
    ),
]


@pytest.mark.parametrize("provider,action,data,method,url,body", CASES, ids=[c[1] for c in CASES])
def test_named_http_requests(provider, action, data, method, url, body):
    observed = []

    async def go():
        def respond(request):
            observed.append(request)
            assert request.method == method
            assert str(request.url) == url
            assert request.headers["authorization"] == "Bearer native-key"
            assert request.extensions["timeout"]["read"] == 9.0
            if provider == "stripe":
                assert request.headers["stripe-version"] == "2026-08-26.dahlia"
                if method == "POST":
                    assert request.headers["idempotency-key"] == "native-intent"
                    assert parse_qs(request.content.decode(), keep_blank_values=True) == body
            elif body is not None:
                assert json.loads(request.content) == body
            return httpx.Response(
                200, json={"id": "native-receipt"}, headers={"request-id": "req_native"}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            auth = (
                ConnectorAuth("api_key", {"api_key": "native-key"})
                if provider == "stripe"
                else ConnectorAuth("private_app_token", {"access_token": "native-key"})
            )
            result = await CLIENT.execute(
                provider,
                action,
                data,
                auth,
                CallOptions(http=http, timeout=9, idempotency_key="native-intent"),
            )
            assert not http.is_closed
            assert result.output_json["body"]["id"] == "native-receipt"

    asyncio.run(go())
    assert len(observed) == 1


@pytest.mark.parametrize(
    "method,header",
    [("personal_api_key", "lin-key"), ("oauth2_authorization_code", "Bearer lin-key")],
)
@pytest.mark.parametrize(
    "action,data,document",
    [
        ("viewer.get", {}, "graphql/viewer/get.graphql"),
        (
            "issues.create",
            {"input": {"teamId": "team-native", "title": "Native issue"}},
            "graphql/issues/create.graphql",
        ),
        ("comments.delete", {"id": "comment-native"}, "graphql/comments/delete.graphql"),
    ],
)
def test_named_graphql_fixed_assets_and_explicit_auth(method, header, action, data, document):
    async def go():
        def respond(request):
            assert request.method == "POST"
            assert str(request.url) == "https://api.linear.app/graphql"
            assert request.headers["authorization"] == header
            sent = json.loads(request.content)
            assert (
                sent["query"]
                == files("stackos_connectors")
                .joinpath("connectors/linear/assets", document)
                .read_text()
            )
            assert sent.get("variables", {}) == data
            assert request.extensions["timeout"]["read"] == 7.0
            return httpx.Response(
                200, json={"data": {"result": {"success": True, "id": "issue-native"}}}
            )

        field = "api_key" if method == "personal_api_key" else "access_token"
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            await CLIENT.execute(
                "linear",
                action,
                data,
                ConnectorAuth(method, {field: "lin-key"}),
                CallOptions(http=http, timeout=7),
            )
            assert not http.is_closed

    asyncio.run(go())


@pytest.mark.parametrize("definition", list(REGISTRY.actions.values()), ids=lambda a: a.key)
def test_every_native_action_rejects_bad_input_or_auth_before_dispatch(definition):
    async def go():
        def forbidden(request):
            pytest.fail("Invalid native input/auth dispatched a provider request")

        async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
            for data, auth in [
                ({"unexpected_host_ref": "provider-object:opaque"}, None),
                ({}, ConnectorAuth("not-a-method", {})),
            ]:
                with pytest.raises(ValidationError):
                    await CLIENT.execute(
                        definition.connector, definition.key, data, auth, CallOptions(http=http)
                    )

    asyncio.run(go())


@pytest.mark.parametrize(
    "method,http_method,url,payload,reply",
    [
        (
            "private_app_token",
            "POST",
            "https://api.hubapi.com/oauth/v2/private-apps/get/access-token-info",
            {"tokenKey": "native-hub-token"},
            {"hubId": 42, "scopes": ["oauth", "crm.objects.contacts.read", "oauth"]},
        ),
        (
            "oauth2_authorization_code",
            "GET",
            "https://api.hubapi.com/integrations/v1/me",
            None,
            {"portalId": 42, "timeZone": "UTC", "currency": "USD"},
        ),
    ],
)
def test_hubspot_probe_is_factual_and_selected_method_controls_wire(
    method, http_method, url, payload, reply
):
    async def go():
        def respond(request):
            assert request.method == http_method and str(request.url) == url
            if payload:
                assert json.loads(request.content) == payload
                assert "authorization" not in request.headers
            else:
                assert request.headers["authorization"] == "Bearer native-hub-token"
            return httpx.Response(200, json=reply)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            integration = HubSpotIntegration(
                payload=b'{"access_token":"native-hub-token"}',
                http=http,
                probe_context=AuthMethodProbeContext(auth_method_key=method),
            )
            result = await integration.test_credentials()
            evidence = result["metadata"]["evidence"]
            assert evidence["account"]["provider_account_id"] == "42"
            assert ("grants" in evidence) == (method == "private_app_token")
            assert "native-hub-token" not in json.dumps(result)

    asyncio.run(go())
