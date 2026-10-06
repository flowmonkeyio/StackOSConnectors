import pytest

from stackos_connectors.connectors.hubspot.signature import verify_signature_v3

_URI = "https://example.test/path?q=%3a%2F%3f%40%21%24%27%28%29%2A%2c%3B&keep=%20%26%3D%25"
_BODY = b'{"hello":"world"}'
_SIGNATURE = "FhSZmmHFCVseGt3WeM6l5TdvzRxyHG3Or+1Udj4PokI="


def test_hubspot_v3_fixed_vector_selective_uri_decoding_and_old_timestamp():
    assert verify_signature_v3("test-secret", "POST", _URI, "1700000000000", _BODY, _SIGNATURE)


@pytest.mark.parametrize(
    "method,uri,timestamp,body,signature",
    [
        ("GET", _URI, "1700000000000", _BODY, _SIGNATURE),
        (
            "POST",
            _URI.replace("example.test", "untrusted.test"),
            "1700000000000",
            _BODY,
            _SIGNATURE,
        ),
        ("POST", _URI.replace("%20", " "), "1700000000000", _BODY, _SIGNATURE),
        ("POST", _URI, "1700000000001", _BODY, _SIGNATURE),
        ("POST", _URI, "1700000000000", _BODY + b" ", _SIGNATURE),
        ("POST", _URI, "1700000000000", _BODY, "v2=obsolete"),
        ("POST", _URI, "1700000000000", _BODY, "non-ascii-\N{SNOWMAN}"),
    ],
)
def test_hubspot_v3_rejects_tampering_and_other_protocols(method, uri, timestamp, body, signature):
    assert not verify_signature_v3("test-secret", method, uri, timestamp, body, signature)
