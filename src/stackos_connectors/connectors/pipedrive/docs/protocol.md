# Pipedrive protocol notes

Distilled from the [StackOS source review](https://github.com/flowmonkeyio/StackOS/blob/3121f4af370fbad273d08a9469d8961de2278534/docs/integration-contracts/gtm-crm.md)
during extraction on 2026-10-05. These provider references do not establish
which actions are installed; use the executable catalog for that.

OAuth access tokens use Authorization: Bearer; personal API tokens use
x-api-token. Select the transport from the declared authentication method.

Pipedrive exposes separate create and update operations, not a universal
native upsert. Notes and activities also have distinct APIs. Do not infer
matching or duplicate-handling policy from an update request.

V2 list endpoints use cursor pagination; preserve next_cursor. The reviewed
list/search endpoints cap pages at 500; consult the particular endpoint for
its limit. Preserve structured success/error fields. Honor 429 responses;
continued rate-limit abuse can result in 403.

Sources: [authentication and API concepts](https://pipedrive.readme.io/docs/core-api-concepts-about-pipedrive-api),
[pagination](https://pipedrive.readme.io/docs/core-api-concepts-pagination),
[rate limits](https://pipedrive.readme.io/docs/core-api-concepts-rate-limiting), and
[response format](https://pipedrive.readme.io/docs/core-api-concepts-responses).
