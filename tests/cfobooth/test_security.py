"""Independent destination, scope, probe and failure controls at native entry points."""

import copy
import json
import traceback

import httpx
import pytest

from stackos_connectors import CallOptions
from stackos_connectors.connectors.cfobooth import (
    catalog_actions,
    declare_action,
    normalize_api_base_url,
)
from stackos_connectors.errors import ConnectorError, IntegrationDownError, ValidationError
from stackos_connectors.probe import probe_credentials

from .conftest import KEY, auth, descriptor


@pytest.mark.parametrize(
    "base",
    [
        "http://localhost:8123/prefix/",
        "http://127.0.0.1:8123/",
        "http://[::1]:8123/",
        "https://owned.test/prefix///",
    ],
)
def test_explicit_native_urls(base):
    assert normalize_api_base_url(base) == base.rstrip("/")


@pytest.mark.parametrize(
    "base",
    [
        None,
        "",
        " ",
        "https://cfobooth.com",
        "https://CFOBOOTH.COM/prefix",
        "https://www.cfobooth.com",
        "http://remote.test",
        "http://127.0.0.2",
        "https://10.0.0.1",
        "https://169.254.169.254",
        "https://user:pass@owned.test",
        "https://@owned.test",
        "https://owned.test?",
        "https://owned.test#",
        "https://owned.test/?key=x",
        "https://owned.test/#part",
        "https://owned.test:99999",
        "https://owned.test:",
        "https://owned.test/a/../b",
        "https://owned.test/%2e%2e",
        "https://owned.test\\@other.test",
        "https://owned.test/\npart",
        "//owned.test",
    ],
)
async def test_invalid_url_never_dispatches(native, base):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("sent"))
    ) as http:
        with pytest.raises((ValidationError, TypeError)):
            await native.execute(
                "cfobooth", "cfobooth.catalog.export", {}, auth(base), CallOptions(http=http)
            )
        with pytest.raises(ValidationError):
            await probe_credentials("cfobooth", auth=auth(base), options=CallOptions(http=http))


@pytest.mark.parametrize(
    "extra",
    [
        {"api_base_url": "https://other.test"},
        {"headers": {"X-API-Key": "wrong"}},
        {"organization_id": "foreign"},
        {"url": "https://other.test"},
        {"path_params": {"organizationId": "foreign"}},
    ],
)
async def test_caller_cannot_override_fixed_binding(native, extra):
    bound = native.register_actions(
        [declare_action("cfobooth.synthetic", descriptor(method="GET"))]
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("sent"))
    ) as http:
        with pytest.raises(ValidationError):
            await bound.execute(
                "cfobooth",
                "cfobooth.synthetic",
                {"path_params": {"recordId": "one"}, **extra},
                auth(),
                CallOptions(http=http),
            )
        with pytest.raises(ValidationError):
            await native.execute(
                "cfobooth",
                "cfobooth.catalog.export",
                {},
                auth(),
                CallOptions(http=http, provider_context=extra),
            )
        with pytest.raises(ValidationError):
            await probe_credentials(
                "cfobooth", auth=auth(), options=CallOptions(http=http, provider_context=extra)
            )


@pytest.mark.parametrize("value", [".", "..", "%2e%2e", "%252e%252e", "a/../b", "", "one\\two"])
async def test_path_traversal_rejects_before_dispatch(native, value):
    bound = native.register_actions(
        [declare_action("cfobooth.synthetic", descriptor(method="GET"))]
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("sent"))
    ) as http:
        with pytest.raises(ValidationError):
            await bound.execute(
                "cfobooth",
                "cfobooth.synthetic",
                {"path_params": {"recordId": value}},
                auth(),
                CallOptions(http=http),
            )


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(version=2),
        lambda d: d.update(catalogHash="bad"),
        lambda d: d["operations"].append(copy.deepcopy(d["operations"][1])),
        lambda d: d.update(operations=[d["operations"][1]] * 513),
        lambda d: d["operations"][1]["schemas"]["query"].update(
            properties={"bad": {"$ref": "https://other.test/schema"}}
        ),
        lambda d: d["operations"][1]["schemas"]["query"].update(unknownKeyword=True),
        lambda d: d["operations"][1]["route"].update(
            path="/api/organizations/:organizationId/../escape"
        ),
    ],
)
async def test_malformed_catalog_is_atomic(native, catalog, change):
    change(catalog["data"])
    with pytest.raises(ValidationError):
        catalog_actions(catalog)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=catalog))
    ) as http:
        with pytest.raises(ConnectorError) as failed:
            await native.execute(
                "cfobooth", "cfobooth.catalog.export", {}, auth(), CallOptions(http=http)
            )
        assert failed.value.metadata_json["provider_executed"] is True
    assert len(native.registry.actions) == 1


@pytest.mark.parametrize("index", [0, 3, 4])
def test_session_only_public_and_specialized_descriptors_do_not_execute(catalog, index):
    with pytest.raises(ValidationError):
        declare_action("cfobooth.excluded", catalog["data"]["operations"][index])


@pytest.mark.parametrize(
    "update",
    [
        {"keyId": "not-uuid"},
        {"organizationId": "53761da2-37bc-40ab-afbb-489fb72a67a1"},
        {"user": {"id": "bad", "email": "bad"}},
        {"expiresAt": "2020-01-01T00:00:00Z"},
        {"expiresAt": "2099-02-30T00:00:00Z"},
        {"expiresAt": "2099-01-01T00:00:00+00:00"},
        {"api_key": KEY},
        {"keyId": None},
    ],
)
async def test_probe_rejects_malformed_foreign_expired_identity(identity, update):
    identity["data"].update(update)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=identity))
    ) as http:
        with pytest.raises(IntegrationDownError) as failed:
            await probe_credentials("cfobooth", auth=auth(), options=CallOptions(http=http))
    assert KEY not in str(failed.value) + str(failed.value.data)


@pytest.mark.parametrize("status", [401, 403, 302, 503])
async def test_revoked_or_redirect_probe_fails_safely(identity, status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status, json={"message": KEY}, headers={"Location": "https://other.test"}
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), follow_redirects=True
    ) as http:
        with pytest.raises(IntegrationDownError) as failed:
            await probe_credentials("cfobooth", auth=auth(), options=CallOptions(http=http))
    assert len(calls) == 1
    assert KEY not in str(failed.value) + str(failed.value.data)


@pytest.mark.parametrize("reply", ["text", ["list"], {"requestId": "bad", "data": {"keyId": KEY}}])
async def test_noncanonical_probe_reply(identity, reply):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=reply))
    ) as http:
        with pytest.raises(IntegrationDownError):
            await probe_credentials("cfobooth", auth=auth(), options=CallOptions(http=http))


async def test_write_timeout_no_replay_and_secret_safe(native):
    calls = []

    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout(KEY, request=request)

    bound = native.register_actions([declare_action("cfobooth.write", descriptor(mutation=True))])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(ConnectorError) as failed:
            await bound.execute(
                "cfobooth",
                "cfobooth.write",
                {"path_params": {"recordId": "one"}, "body": {"name": "value"}},
                auth(),
                CallOptions(http=http),
            )
    assert len(calls) == 1
    assert failed.value.metadata_json["outcome_unknown"] is True
    assert failed.value.metadata_json["retry_safe"] is False
    assert KEY not in "".join(traceback.format_exception(failed.value)) + json.dumps(
        failed.value.output_json
    )


@pytest.mark.parametrize("key", ["", "one\ntwo", "one two", "one\rtwo"])
async def test_invalid_header_key_does_not_dispatch(native, key):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("sent"))
    ) as http:
        with pytest.raises(ValidationError):
            await native.execute(
                "cfobooth", "cfobooth.catalog.export", {}, auth(key=key), CallOptions(http=http)
            )
        with pytest.raises(ValidationError):
            await probe_credentials("cfobooth", auth=auth(key=key), options=CallOptions(http=http))


async def test_future_expiry_and_explicit_replacement(identity):
    identity["data"]["expiresAt"] = "2099-01-01T00:00:00.000Z"
    seen = []

    def handle(request):
        seen.append(request.headers["x-api-key"])
        return httpx.Response(200, json=identity)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        first = await probe_credentials("cfobooth", auth=auth(), options=CallOptions(http=http))
        identity["data"]["keyId"] = "53761da2-37bc-40ab-afbb-489fb72a67a2"
        replacement = await probe_credentials(
            "cfobooth", auth=auth(key="synthetic-replacement"), options=CallOptions(http=http)
        )
    assert seen == [KEY, "synthetic-replacement"]
    assert first["identity"]["keyId"] != replacement["identity"]["keyId"]


async def test_canonical_error_canary_is_redacted(native):
    item = descriptor(mutation=True)
    bound = native.register_actions([declare_action("cfobooth.write", item)])
    error = {"requestId": "safe-error", "error": {"code": "forbidden", "message": KEY}}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(403, json=error))
    ) as http:
        with pytest.raises(ConnectorError) as failed:
            await bound.execute(
                "cfobooth",
                "cfobooth.write",
                {"path_params": {"recordId": "one"}, "body": {"name": "x"}},
                auth(),
                CallOptions(http=http),
            )
    assert failed.value.provider_error["error"]["code"] == "forbidden"
    assert failed.value.provider_error["requestId"] == "safe-error"
    assert KEY not in json.dumps(failed.value.output_json) + str(failed.value.metadata_json)
