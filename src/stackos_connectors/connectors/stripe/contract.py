"""Fixed provider action routes; callers cannot select arbitrary endpoints."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StripeActionSpec:
    method: str
    path: str
    object_type: str | None = None
    list_item_type: str | None = None

    @property
    def write(self) -> bool:
        return self.method == "POST"


STRIPE_ACTION_SPECS: dict[str, StripeActionSpec] = {
    "stripe.products.list": StripeActionSpec("GET", "/products", list_item_type="stripe.product"),
    "stripe.products.retrieve": StripeActionSpec("GET", "/products/{product_id}", "stripe.product"),
    "stripe.prices.list": StripeActionSpec("GET", "/prices", list_item_type="stripe.price"),
    "stripe.prices.retrieve": StripeActionSpec("GET", "/prices/{price_id}", "stripe.price"),
    "stripe.customers.create": StripeActionSpec("POST", "/customers", "stripe.customer"),
    "stripe.customers.update": StripeActionSpec(
        "POST", "/customers/{customer_id}", "stripe.customer"
    ),
    "stripe.customers.retrieve": StripeActionSpec(
        "GET", "/customers/{customer_id}", "stripe.customer"
    ),
    "stripe.customers.list": StripeActionSpec(
        "GET", "/customers", list_item_type="stripe.customer"
    ),
    "stripe.customers.tax-ids.list": StripeActionSpec(
        "GET", "/customers/{customer_id}/tax_ids", list_item_type="stripe.tax-id"
    ),
    "stripe.customers.tax-ids.create": StripeActionSpec(
        "POST", "/customers/{customer_id}/tax_ids", "stripe.tax-id"
    ),
    "stripe.customers.tax-ids.retrieve": StripeActionSpec(
        "GET", "/customers/{customer_id}/tax_ids/{tax_id_id}", "stripe.tax-id"
    ),
    "stripe.invoices.create": StripeActionSpec("POST", "/invoices", "stripe.invoice"),
    "stripe.invoices.update": StripeActionSpec("POST", "/invoices/{invoice_id}", "stripe.invoice"),
    "stripe.invoice-items.create": StripeActionSpec("POST", "/invoiceitems", "stripe.invoice-item"),
    "stripe.invoice-items.list": StripeActionSpec(
        "GET", "/invoiceitems", list_item_type="stripe.invoice-item"
    ),
    "stripe.invoices.finalize": StripeActionSpec(
        "POST", "/invoices/{invoice_id}/finalize", "stripe.invoice"
    ),
    "stripe.invoices.send": StripeActionSpec(
        "POST", "/invoices/{invoice_id}/send", "stripe.invoice"
    ),
    "stripe.invoices.mark-paid-out-of-band": StripeActionSpec(
        "POST", "/invoices/{invoice_id}/pay", "stripe.invoice"
    ),
    "stripe.invoices.attach-payment": StripeActionSpec(
        "POST", "/invoices/{invoice_id}/attach_payment", "stripe.invoice"
    ),
    "stripe.invoices.retrieve": StripeActionSpec("GET", "/invoices/{invoice_id}", "stripe.invoice"),
    "stripe.invoices.list": StripeActionSpec("GET", "/invoices", list_item_type="stripe.invoice"),
    "stripe.invoice-payments.list": StripeActionSpec(
        "GET", "/invoice_payments", list_item_type="stripe.invoice-payment"
    ),
    "stripe.payment-intents.retrieve": StripeActionSpec(
        "GET", "/payment_intents/{payment_intent_id}", "stripe.payment-intent"
    ),
    "stripe.payment-records.report": StripeActionSpec(
        "POST", "/payment_records/report_payment", "stripe.payment-record"
    ),
    "stripe.payment-records.retrieve": StripeActionSpec(
        "GET", "/payment_records/{payment_record_id}", "stripe.payment-record"
    ),
    "stripe.payment-records.list": StripeActionSpec(
        "GET", "/payment_records", list_item_type="stripe.payment-record"
    ),
    "stripe.charges.retrieve": StripeActionSpec("GET", "/charges/{charge_id}", "stripe.charge"),
    "stripe.charges.list": StripeActionSpec("GET", "/charges", list_item_type="stripe.charge"),
    "stripe.disputes.list": StripeActionSpec("GET", "/disputes", list_item_type="stripe.dispute"),
    "stripe.disputes.retrieve": StripeActionSpec("GET", "/disputes/{dispute_id}", "stripe.dispute"),
    "stripe.balance-transactions.retrieve": StripeActionSpec(
        "GET", "/balance_transactions/{balance_transaction_id}", "stripe.balance-transaction"
    ),
    "stripe.balance-transactions.list": StripeActionSpec(
        "GET", "/balance_transactions", list_item_type="stripe.balance-transaction"
    ),
    "stripe.refunds.retrieve": StripeActionSpec("GET", "/refunds/{refund_id}", "stripe.refund"),
    "stripe.refunds.list": StripeActionSpec("GET", "/refunds", list_item_type="stripe.refund"),
    "stripe.balance.retrieve": StripeActionSpec("GET", "/balance"),
}
