# Clay protocol notes

Distilled from the [StackOS source review](https://github.com/flowmonkeyio/StackOS/blob/3121f4af370fbad273d08a9469d8961de2278534/docs/integration-contracts/gtm-prospecting-outbound.md)
during extraction on 2026-10-05. These provider references do not establish
which actions are installed; use the executable catalog for that.

A table webhook submission is asynchronous: acceptance does not mean that
enrichment finished or that enriched rows were returned. Results arrive
through the separately configured callback/export path.

The reviewed public integration is table webhooks plus outbound HTTP
actions. Enterprise People/Company endpoints need their own endpoint and
schema contract; do not infer a universal synchronous search API or common
pagination/error format from webhook behavior.

Source: [Clay's API and webhook integration guide](https://university.clay.com/docs/using-clay-as-an-api).
