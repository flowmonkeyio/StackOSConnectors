# Stripe protocol and response limits

Source: [reviewed source contract](https://github.com/flowmonkeyio/StackOS/blob/3121f4af370fbad273d08a9469d8961de2278534/docs/integration-contracts/stripe.md).
Condensed during extraction on 2026-10-05; provider documentation was not
reverified live. Installed actions and schemas are defined by the catalog.

The reviewed transport pins Stripe-Version: 2026-08-26.dahlia and uses a
restricted secret API key. Resource permissions remain provider-enforced.
Lists use bounded limit/starting_after pagination; a page is not a full
inventory. Preserve request IDs, status, structured error fields and rate-limit
context. A lost POST response can leave an unknown effect even with a
caller-supplied idempotency key; inspect its result rather than blindly
recreating the operation.

Invoice creation, finalization, sending and payment attachment are distinct
provider operations. auto_advance=false disables automatic advancement.
effective_at controls the displayed issue date, not the creation, due or
payment timestamp. Price-backed invoice lines let Stripe calculate totals;
amount_due can differ from total/subtotal because of adjustments.

The reviewed public invoice contract has important output limits:

- hosted_invoice_url and invoice_pdf are null before finalization.
- create_preview does not accept a saved invoice ID and is not a saved-draft
  PDF endpoint.
- The send endpoint has no to/cc/bcc recipient override or hide-payment-link
  parameter. Test-mode sends do not send email.
- Primary customer/invoice email fields cannot enumerate additional billing
  recipients. Finalization freezes customer and custom-field snapshots.
- us_bank_account means ACH debit; customer_balance means Stripe-managed
  bank transfer. Neither means a direct deposit into an arbitrary bank account.
- Customer tax-ID verification is asynchronous; successful creation does not
  imply verified status.

InvoicePayment records link invoices to payment objects; missing linkage
does not establish a match. PaymentRecord reporting records an existing payment,
not a new charge. Attaching a payment and marking paid_out_of_band have
different meanings; the latter does not initiate a charge or represent partial
payment. A paid invoice link alone does not prove the underlying payment facts.

Use a freshly returned invoice_pdf URL for an available finalized PDF, without
sending Stripe API credentials to the download endpoint. Finalized status does
not guarantee that a URL download succeeds. Do not invent PDF URLs or report a
preview as the exact saved invoice document.

Sources: [authentication](https://docs.stripe.com/api/authentication),
[versioning](https://docs.stripe.com/api/versioning),
[idempotency](https://docs.stripe.com/api/idempotent_requests),
[low-level errors](https://docs.stripe.com/error-low-level),
[pagination](https://docs.stripe.com/api/pagination),
[invoices](https://docs.stripe.com/api/invoices/object),
[preview](https://docs.stripe.com/api/invoices/create_preview),
[send](https://docs.stripe.com/api/invoices/send),
[tax-ID verification](https://docs.stripe.com/billing/customer/tax-ids),
[InvoicePayment](https://docs.stripe.com/api/invoice-payment),
[PaymentRecord](https://docs.stripe.com/api/payment-record/report),
[attach payment](https://docs.stripe.com/api/invoices/attach_payment), and
[the reviewed OpenAPI snapshot](https://github.com/stripe/openapi/blob/9ac29c7795ab21c7711b4bc25bb2dd739552a5fa/latest/openapi.spec3.json).
