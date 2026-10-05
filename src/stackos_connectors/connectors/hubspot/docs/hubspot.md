# HubSpot protocol

Source: [reviewed source contract](https://github.com/flowmonkeyio/StackOS/blob/3121f4af370fbad273d08a9469d8961de2278534/docs/integration-contracts/hubspot.md).
Condensed during extraction on 2026-10-05; provider documentation was not
reverified live. Installed actions and schemas are defined by the catalog.

HubSpot supports OAuth access tokens and private-app tokens. The private-app
token inspection endpoint is
`POST /oauth/v2/private-apps/get/access-token-info`. Returned account/scopes
are provider evidence; requested scopes do not prove granted permissions.
Feature-specific licenses and entitlements can restrict an otherwise
authenticated request.

CRM properties use provider property names. Batch upsert requires an
appropriate unique id property, or contact email where supported; it is not
a universal single-record upsert. Associations use their object IDs and
association type/label definitions. Preserve per-row batch errors.

CRM search uses cursor pagination, at most 200 objects per page and 10,000
results per query in the reviewed contract. Preserve paging and correlation
IDs. A 429 response can carry rate policy details; OAuth responses do not
supply every daily rate-limit header. Batch create can return multi-status.

Exports are asynchronous: creating a job is not completion. The reviewed
2026-03 export API separates create/status/result operations; consume download
results only when complete, preserving partial/failure state. Signed download
URLs are short-lived. Export authorization requires crm.export and a
Super Admin OAuth installer.

Transactional email requires its provider scope and applicable Marketing
subscription/add-on. The returned eventId has separate id and created fields;
acceptance is not delivery evidence. These provider requirements are
independent of any caller's workflow.

Sources: [OAuth tokens](https://developers.hubspot.com/docs/api-reference/latest/authentication/manage-oauth-tokens),
[private apps](https://developers.hubspot.com/docs/apps/legacy-apps/private-apps/overview),
[CRM objects](https://developers.hubspot.com/docs/api-reference/legacy/crm/using-object-apis),
[CRM search](https://developers.hubspot.com/docs/api-reference/latest/crm/search-the-crm),
[usage limits](https://developers.hubspot.com/docs/developer-tooling/platform/usage-guidelines),
[exports](https://developers.hubspot.com/docs/api-reference/latest/crm/exports/guide),
and [transactional email](https://developers.hubspot.com/docs/api-reference/latest/marketing/transactional-emails/guide).
