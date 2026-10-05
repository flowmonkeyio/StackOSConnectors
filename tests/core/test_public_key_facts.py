from stackos_connectors.redaction import redact_secret_values, redact_secrets


def test_public_usage_and_expiry_facts_keep_values():
    value = {
        "limits_and_usage": {
            "units_usage_api_key": 1234,
            "units_limit_api_key": 400000,
            "api_key_expiration_date": "2027-01-01",
        }
    }
    assert redact_secrets(value) == value


def test_fact_field_values_still_receive_all_redaction_layers():
    value = {
        "units_usage_api_key": {"api_key": "secret", "diagnostic": "api_key=secret"},
        "units_limit_api_key": ["Bearer secret", {"password": "secret"}],
        "api_key_expiration_date": "2027-01-01 secret-echo",
        "api_key": "secret",
        "prefix_units_usage_api_key": "secret",
        "units_usage_api_key_suffix": "secret",
        "units-usage-api-key": "secret",
    }
    result = redact_secret_values(redact_secrets(value), ("secret-echo", "secret"))
    assert result["units_usage_api_key"] == {
        "api_key": "[redacted]",
        "diagnostic": "api_key=[redacted]",
    }
    assert result["units_limit_api_key"] == ["Bearer [redacted]", {"password": "[redacted]"}]
    assert result["api_key_expiration_date"] == "2027-01-01 [redacted]"
    for key in (
        "api_key",
        "prefix_units_usage_api_key",
        "units_usage_api_key_suffix",
        "units-usage-api-key",
    ):
        assert result[key] == "[redacted]"
