# ElevenLabs Voice Library

Both actions require a resolved ElevenLabs API key with access to the requested
route. Search reads one page. The caller selects the voice and invokes the add
action explicitly.

| Native action | ElevenLabs route |
| --- | --- |
| `elevenlabs.library.search` | `GET /v1/shared-voices` |
| `elevenlabs.library.add` | `POST /v1/voices/add/{public_user_id}/{voice_id}` |

Search accepts optional `search`, zero-based `page` (default `0`) and `page_size`
from 1 to 100 (default `30`). The result contains `voices` and the provider's
`has_more` flag. Use `public_owner_id` and `voice_id` to identify each result,
including when several rows share a `name`. The connector preserves response
order and duplicates. `preview_url`, `notice_period`, `rate` and `fiat_rate`
remain absent when omitted and keep explicit nulls. `rate` is a credit-rate
multiplier. `fiat_rate` is USD per 1,000 credits, with explicit null indicating
default pricing. [List shared voices](https://elevenlabs.io/docs/api-reference/voices/voice-library/get-shared)

Add takes `public_owner_id`, `voice_id` and `new_name`. It places the owner ID in
the provider's `public_user_id` path segment and sends `new_name` in the JSON
body. The result contains the `voice_id` returned by the add endpoint. Each
invocation sends at most one POST. A provider 403 surfaces as `permission_denied`.
On ambiguous failures, inspect `outcome_unknown` before deciding whether to
submit another request. [Add shared voice](https://elevenlabs.io/docs/api-reference/voices/voice-library/share)
