# Cloudflare DNS protocol

Source: [reviewed source contract](https://github.com/flowmonkeyio/StackOS/blob/3121f4af370fbad273d08a9469d8961de2278534/docs/integration-contracts/cloudflare-dns.md).
Condensed during extraction on 2026-10-05; provider documentation was not
reverified live. Installed actions and schemas are defined by the catalog.

Requests use `https://api.cloudflare.com/client/v4`,
`Authorization: Bearer <token>` and JSON. Zone discovery needs Zone Read;
DNS reads accept DNS Read or DNS Write; mutations need DNS Write.
Token verification proves token activity, not those resource permissions.

Zone discovery uses `GET /zones`. Record operations use
`/zones/{zone_id}/dns_records` and its `/{dns_record_id}` child.
POST creates, PATCH edits, PUT replaces and DELETE deletes. These are distinct
operations; a mutation does not require an implicit preliminary read.

Preserve documented dotted filter names such as `name.contains` and
`comment.present`, and list `result_info` for pagination. Record bodies
use provider fields and type-specific `content`/`data`; do not invent a
record grammar beyond the declared schema. TTL `1` means automatic.
Other TTL bounds can depend on plan restrictions.

Successful common envelopes include `success`, `errors`, `messages`
and `result`; deletion can return the documented minimal `result.id`
receipt. HTTP 200 alone does not prove success. Preserve provider errors,
CF-Ray, rate-limit headers and Retry-After where supplied.

These DNS endpoints have no documented idempotency-key guarantee. A transport
failure or an untrustworthy success receipt after a mutation can leave the
outcome unknown; do not automatically repeat it. API acceptance does not
prove global DNS propagation.

Sources: [zones](https://developers.cloudflare.com/api/resources/zones/methods/list/),
[DNS records](https://developers.cloudflare.com/api/resources/dns/subresources/records/),
[token permissions](https://developers.cloudflare.com/fundamentals/api/reference/permissions/),
[rate limits](https://developers.cloudflare.com/fundamentals/api/reference/limits/),
and [OpenAPI schemas](https://github.com/cloudflare/api-schemas).
