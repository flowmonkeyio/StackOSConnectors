# Outreach protocol notes

Distilled from the [StackOS source review](https://github.com/flowmonkeyio/StackOS/blob/3121f4af370fbad273d08a9469d8961de2278534/docs/integration-contracts/gtm-prospecting-outbound.md)
during extraction on 2026-10-05. These provider references do not establish
which actions are installed; use the executable catalog for that.

Use JSON:API payloads and Content-Type: application/vnd.api+json.
Collections return data and links, with optional meta. Follow cursor links
when provided; the reviewed contract recommends page[size] with count=false
and caps offset page[limit] at 1000.

Sequence enrollment creates a sequenceState with provider relationship
identifiers. Preserve top-level errors and their identity, title, details
and HTTP status instead of flattening every error into one message.

Sources: [requests and pagination](https://developers.outreach.io/api/making-requests),
[common patterns](https://developers.outreach.io/api/common-patterns/), and
[Sequence State](https://developers.outreach.io/api/reference/tag/Sequence-State/).
