# AIGNC protocol

Source: [reviewed source contract](https://github.com/flowmonkeyio/StackOS/blob/3121f4af370fbad273d08a9469d8961de2278534/docs/integration-contracts/aignc.md).
Condensed during extraction on 2026-10-05; provider documentation was not
reverified live. Installed actions and schemas are defined by the catalog.

The supplied service contract uses `https://cli-api.f2nd.com/v1` with
`Authorization: Bearer <api key>`. This service URL is not a documented
signup, console or API-key acquisition page. The source review relies on a
supplier-provided guide; it establishes no public provider documentation site.

`GET /models` lists provider model IDs. `POST /chat/completions` accepts
explicit model and messages for synchronous, nonstreaming responses.
Provider model names do not establish equivalence with similarly named
Google, Anthropic or OpenAI models. Requested and returned model IDs can differ.

The reviewed image model is `gemini-3.1-flash-image`. JPEG data can appear
inside assistant content or as a data URI in
`message.images[0].image_url.url`. Observed dimensions are not a size
guarantee. The reviewed audio models are `gemini-3.8-flash` and
`gemini-3.7-flash`; the guide names WAV, MP3, AAC, FLAC and OGG input.
Generated speaker labels do not prove diarization, timestamps or identity.

Google grounding is requested with `tools: [{google_search: {}}]`.
Enabling it does not prove a search occurred. Preserve the reported
`usage.google_searches`; absent is unknown, not zero. Preserve grounding
chunk order and support indices. Source titles do not resolve redirect URLs.
Offsets refer to provider text and may no longer align after redaction.

Preserve `cf-aig-log-id` as provider request evidence. The supplied guide
does not establish rate limits, idempotent retry, pagination, retention,
moderation, region, commercial-use terms or financial rates. Token and search
counts are usage observations, not monetary prices.
