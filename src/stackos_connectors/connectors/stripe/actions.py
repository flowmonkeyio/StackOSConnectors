"""Named native provider actions with resolved authentication and plain data."""

from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import AsyncExitStack
from typing import Any
from urllib.parse import quote

import httpx

from stackos_connectors import ConnectorError, ConnectorRequest, ConnectorResult, ValidationIssue
from stackos_connectors.contracts import thaw
from stackos_connectors.errors import IntegrationDownError, RateLimitedError
from stackos_connectors.redaction import redact_secrets

from .contract import STRIPE_ACTION_SPECS, StripeActionSpec
from .integration import StripeIntegration


class StripeActionConnector:
    key = "stripe"

    def validate(self, request):
        spec = STRIPE_ACTION_SPECS[request.action_key]
        if spec.write:
            key = request.options.idempotency_key
            if not isinstance(key, str) or not key.strip():
                return [
                    ValidationIssue(
                        path="$.options.idempotency_key",
                        message="Stripe POST requires an idempotency key",
                    )
                ]
            if len(key) > 255:
                return [
                    ValidationIssue(
                        path="$.options.idempotency_key",
                        message="Stripe idempotency key must be at most 255 characters",
                    )
                ]
        return []

    def estimate_cost_cents(self, request):
        return 0

    async def execute(self, request):
        spec = STRIPE_ACTION_SPECS[request.action_key]
        assert request.auth is not None
        path = spec.path.format(
            **{
                key: quote(str(value), safe="")
                for key, value in request.input_json.items()
                if key.endswith("_id")
            }
        )
        async with AsyncExitStack() as stack:
            http = request.options.http
            if http is None:
                http = await stack.enter_async_context(httpx.AsyncClient(timeout=30.0))
            integration = StripeIntegration(
                payload=json.dumps(thaw(request.auth.fields)).encode(),
                http=http,
                auth_method_key=request.auth.method,
                timeout=request.options.timeout,
                rate_limiter=request.options.rate_limiter,
            )
            try:
                result = await integration.request(
                    method=spec.method,
                    path=path,
                    op=request.action_key,
                    params=_params_for(request, spec) or None,
                    form=_form_for(request, spec) or None,
                    idempotency_key=request.options.idempotency_key if spec.write else None,
                )
            except (IntegrationDownError, RateLimitedError) as exc:
                raise _connector_error(exc) from None
        return ConnectorResult(
            output_json={"body": result.data},
            metadata_json={
                "vendor": "stripe",
                "operation": request.action_key,
                **(result.metadata or {}),
            },
        )


def _connector_error(exc: IntegrationDownError | RateLimitedError) -> ConnectorError:
    data = exc.data if isinstance(exc.data, Mapping) else {}
    status = data.get("status")
    provider_status_code = status if isinstance(status, int) else None
    provider_error = data.get("provider_error")
    safe_error = dict(provider_error) if isinstance(provider_error, Mapping) else {}
    safe_error.setdefault(
        "reason_code", "rate_limited" if isinstance(exc, RateLimitedError) else "provider_failure"
    )
    safe_error["outcome_unknown"] = bool(data.get("outcome_unknown"))
    safe_error["retry_safe"] = bool(data.get("retry_safe"))
    if isinstance(data.get("retry_after"), int | float):
        safe_error["retry_after"] = data["retry_after"]
    if isinstance(data.get("recovery"), str):
        safe_error["recovery"] = data["recovery"][:500]
    output_json = {
        "status": "failed",
        "outcome_unknown": bool(data.get("outcome_unknown")),
        "retry_safe": bool(data.get("retry_safe")),
    }
    if provider_status_code is not None:
        output_json["provider_status_code"] = provider_status_code
    if safe_error:
        output_json["provider_error"] = redact_secrets(safe_error)
    return ConnectorError(
        "Stripe action failed",
        provider_status_code=provider_status_code,
        provider_error=redact_secrets(safe_error),
        output_json=output_json,
    )


STRIPE_DEFAULT_LIMIT = 25
_CUSTOMER_ADDRESS_FIELDS = ("line1", "line2", "city", "state", "postal_code", "country")


def _params_for(request: ConnectorRequest, spec: StripeActionSpec) -> dict[str, Any]:
    if spec.write:
        return {}
    payload = request.input_json
    params: dict[str, Any] = {}
    if "expand" in payload:
        params["expand[]"] = payload["expand"]
    if spec.list_item_type:
        params["limit"] = payload.get("limit", STRIPE_DEFAULT_LIMIT)
        if "starting_after" in payload:
            params["starting_after"] = payload["starting_after"]
        if request.action_key in {"stripe.products.list", "stripe.prices.list"}:
            if "active" in payload:
                params["active"] = "true" if payload["active"] else "false"
            if request.action_key == "stripe.prices.list":
                if "product_id" in payload:
                    params["product"] = payload["product_id"]
                params.update(_copy_fields(payload, "currency", "type"))
        if request.action_key == "stripe.invoices.list" and "status" in payload:
            params["status"] = payload["status"]
        if request.action_key == "stripe.invoices.list":
            if "customer_id" in payload:
                params["customer"] = payload["customer_id"]
            for bound in ("gte", "lte"):
                if f"created_{bound}" in payload:
                    params[f"created[{bound}]"] = payload[f"created_{bound}"]
        if request.action_key == "stripe.invoice-items.list":
            for field in ("invoice", "customer"):
                if f"{field}_id" in payload:
                    params[field] = payload[f"{field}_id"]
        if request.action_key == "stripe.disputes.list":
            for field in ("charge", "payment_intent"):
                if f"{field}_id" in payload:
                    params[field] = payload[f"{field}_id"]
        if request.action_key == "stripe.customers.list":
            params["email"] = payload["email"]
        if request.action_key == "stripe.invoice-payments.list":
            params["invoice"] = payload["invoice_id"]
        if request.action_key == "stripe.charges.list" and "customer_id" in payload:
            params["customer"] = payload["customer_id"]
        if request.action_key == "stripe.charges.list" and "payment_intent_id" in payload:
            params["payment_intent"] = payload["payment_intent_id"]
        if request.action_key == "stripe.refunds.list" and "charge_id" in payload:
            params["charge"] = payload["charge_id"]
    return params


def _form_for(request: ConnectorRequest, spec: StripeActionSpec) -> dict[str, Any]:
    if not spec.write:
        return {}
    payload = request.input_json
    if request.action_key == "stripe.customers.create":
        return _copy_fields(payload, "email", "name", "description")
    if request.action_key == "stripe.customers.update":
        form = _copy_fields(payload, "name", "email", "phone")
        address = payload.get("address")
        if isinstance(address, Mapping):
            form.update(
                {
                    f"address[{key}]": address[key]
                    for key in _CUSTOMER_ADDRESS_FIELDS
                    if key in address
                }
            )
        if "invoice_settings" in payload:
            form.update(
                _custom_fields_form(
                    payload["invoice_settings"]["custom_fields"], "invoice_settings[custom_fields]"
                )
            )
        return form
    if request.action_key == "stripe.customers.tax-ids.create":
        return _copy_fields(payload, "type", "value")
    if request.action_key == "stripe.invoices.create":
        return {
            "customer": payload["customer_id"],
            "collection_method": payload["collection_method"],
            "currency": payload["currency"],
            "days_until_due": payload["days_until_due"],
            "auto_advance": "true" if payload["auto_advance"] else "false",
            **_copy_fields(payload, "effective_at"),
            **({f"metadata[{key}]": value for key, value in payload.get("metadata", {}).items()}),
            **_copy_fields(payload, "description"),
        }
    if request.action_key == "stripe.invoices.update":
        form = _copy_fields(payload, "footer", "description")
        if "custom_fields" in payload:
            form.update(_custom_fields_form(payload["custom_fields"], "custom_fields"))
        if "payment_method_types" in payload:
            form["payment_settings[payment_method_types][]"] = payload["payment_method_types"]
        return form
    if request.action_key == "stripe.invoices.finalize":
        return {"auto_advance": "true" if payload["auto_advance"] else "false"}
    if request.action_key == "stripe.invoices.mark-paid-out-of-band":
        return {"paid_out_of_band": "true"}
    if request.action_key == "stripe.invoices.attach-payment":
        if "payment_intent_id" in payload:
            return {"payment_intent": payload["payment_intent_id"]}
        return {"payment_record": payload["payment_record_id"]}
    if request.action_key == "stripe.payment-records.report":
        return {
            "amount_requested[currency]": payload["currency"],
            "amount_requested[value]": payload["amount"],
            "initiated_at": payload["initiated_at"],
            "outcome": payload["outcome"],
            "guaranteed[guaranteed_at]": payload["guaranteed_at"],
            "payment_method_details[type]": payload["payment_method_type"],
            "payment_method_details[custom][display_name]": payload["payment_method_display_name"],
            "processor_details[type]": payload["processor_type"],
            "processor_details[custom][payment_reference]": payload["payment_reference"],
            "customer_details[customer]": payload["customer_id"],
        }
    if request.action_key == "stripe.invoice-items.create":
        form = {"customer": payload["customer_id"], "invoice": payload["invoice_id"]}
        if "price_id" in payload:
            form.update(
                {
                    "pricing[price]": payload["price_id"],
                    "quantity": payload["quantity"],
                    **_copy_fields(payload, "currency", "description"),
                }
            )
        else:
            form.update(_copy_fields(payload, "amount", "currency", "description"))
        return form
    return {}


def _copy_fields(payload: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    return {key: value for key in keys if (value := payload.get(key)) is not None}


def _custom_fields_form(fields: list[Mapping[str, str]], prefix: str) -> dict[str, Any]:
    if not fields:
        return {prefix: ""}
    return {
        f"{prefix}[{index}][{key}]": entry[key]
        for index, entry in enumerate(fields)
        for key in ("name", "value")
    }
