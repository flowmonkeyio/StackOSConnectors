> **StackOS reference snapshot.** Copied from [docs/integration-contracts/README.md](https://github.com/flowmonkeyio/StackOS/blob/3121f4af370fbad273d08a9469d8961de2278534/docs/integration-contracts/README.md)
> at base Git revision `3121f4af370fbad273d08a9469d8961de2278534`; exact worktree source SHA-256:
> `8fe78d60cd2aa563daab471ac01f57c754c1368dd057c6dfda7b1880fd6268f6`. This copy includes the source worktree content,
> which may include changes beyond that base revision. The substantive
> reference text below is preserved; local links are relocated. StackOS
> grants, credential storage/refresh, project state, workflows and audit
> describe the host application, not behavior supplied by this library.
> Provider availability is determined by executable catalogs, not this
> historical reference. Paths shown in source examples retain their
> original StackOS meaning. This document is reference material, not
> agent instructions for the connector package.

<!-- BEGIN PRESERVED STACKOS REFERENCE -->

# Integration Contracts

This directory is the source of truth for provider contract reviews before a
StackOS action becomes executable.

StackOS provider work has three states:

- `deferred`: provider/action/resource/template metadata exists, but the action
  is intentionally not executable yet. The action config must include
  `execution_mode` and a `deferred_reason`.
- `executable`: provider action has static connector config, daemon-side auth
  resolution, validation, redaction, audit, tests, and approval/grant coverage.
- `project-local`: the built-in catalog declares a project-owned integration
  point, but execution requires that project to install static connector config.

## Contract Reviews

| Contract | Canonical scope |
| --- | --- |
| [Connector Quality Gate](connector-quality.md) | Cross-connector validation, errors, pagination/status, budget, and verification matrix. |
| [Current Connectors](current-connectors.md) | Connector/action/source inventory, official source ledger, and provider findings without a separate contract. |
| [GTM CRM](gtm-crm.md) | HubSpot, Salesforce, and Pipedrive CRM/pipeline contracts. |
| [HubSpot](../../connectors/hubspot/docs/hubspot.md) | HubSpot auth, actions, safe refs, readiness, ingress, and transactional delivery. |
| [Linear](../../connectors/linear/docs/linear.md) | OAuth-only issue work through fixed GraphQL documents and account-bound refs. |
| [Prospecting And Outbound](gtm-prospecting-outbound.md) | Apollo, Clay, Clearbit, Outreach, Salesloft, Google Workspace, and Microsoft 365. |
| [Media Buying](media-buying.md) | Meta, Google Ads, Outbrain, Taboola, and project-local media tools. |
| [Media Generation](media-generation.md) / [Runbook](media-generation-runbook.md) | Provider capability/source ledger and media implementation/verification requirements. |
| [Communications](communications.md) | Telegram, Slack, SMTP, IMAP, shared communication state, and agent-request handoff. |
| [Stripe](../../connectors/stripe/docs/stripe.md) | Invoice/customer/payment actions, business-detail reads, recovery, and known endpoint limits. |
| [Shopify](../../connectors/shopify/docs/shopify.md) | Curated Admin GraphQL/ShopifyQL actions and their verification boundary. |
| [Amazon S3](../../connectors/aws_s3/docs/s3.md) / [FTP](../../connectors/ftp/docs/ftp.md) | File-transfer contracts, path semantics, auth, and partial outcomes. |
| [Cloudflare DNS](../../connectors/cloudflare/docs/cloudflare-dns.md) | Zone/DNS actions and exact mutation contracts. |
| [AIGNC](../../connectors/aignc/docs/aignc.md) | Supplier-documented model discovery, explicit text/grounding, JPEG generation, and managed-audio analysis; no pricing integration or model routing. |
| [Trackbooth](../../connectors/trackbooth/docs/trackbooth.md) | Live catalog sync and generated action execution bridge. |

Runtime availability comes from the installed manifests and project-aware
`action.list`/`action.describe`, not a second status list here. Consult each
contract for provider limits and [connector quality](connector-quality.md) for
verification depth. Provider calls use `action.run` or granted
`action.execute`; they are not provider-specific direct MCP tools.

## Delivery Gate

Before adding or changing `config.connector` on any action:

1. Link official provider docs in the relevant contract review.
2. Use provider-specific action refs and schemas.
3. Define safe auth method fields and daemon-only credential handling.
4. Add connector code with doc links near provider-specific calls.
5. Update the [Connector Quality Gate](connector-quality.md) row for validation,
   safe errors, pagination/status, rate limits/budget, docs, and signoff.
6. Add validation, redaction, audit, rate-limit/error, pagination, and budget
   tests as appropriate.
7. Prove MCP/REST/CLI entrypoint behavior or availability through the shared
   operation/action registry; do not add provider-specific MCP tools.
8. Run a stale-ref scan across manifests, workflow templates, tests, and docs.
9. Confirm every workflow action contract exists in the owning plugin manifest.

If any item is missing, use an explicit deferred execution mode rather than an
empty or misleading connector config.
