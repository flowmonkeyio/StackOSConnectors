# ElevenLabs discovery

Pass resolved `ConnectorAuth(method="api_key", fields={"api_key": key})` to
the native client. Requests send the key in `xi-api-key` to
`https://api.elevenlabs.io`. The caller owns key custody and selection.
[ElevenLabs API authentication](https://elevenlabs.io/docs/api-reference/authentication)

See [speech and forced alignment](audio.md) for the paid audio actions.
See [Voice Library](library.md) for shared voice search and explicit addition.

| Native action | ElevenLabs route | Result |
| --- | --- | --- |
| `elevenlabs.subscription.get` | `GET /v1/user/subscription` | `tier`, `character_count` and `character_limit` |
| `elevenlabs.models.list` | `GET /v1/models` | `models`, including non-TTS models |
| `elevenlabs.voices.list` | `GET /v2/voices` | One page of available voices |
| `elevenlabs.voices.settings.default.get` | `GET /v1/voices/settings/default` | Provider defaults |
| `elevenlabs.voices.settings.get` | `GET /v1/voices/{voice_id}/settings` | Saved settings for the required `voice_id` |

`probe_credentials("elevenlabs", auth=...)` uses the same subscription reader
as the action. It sends one GET and returns `ok`, `vendor` and the three
subscription fields. A count or limit of zero is returned as an integer. A
restricted key may be denied access to the subscription. The probe exposes the
HTTP status in `IntegrationDownError.data`, and the action exposes it in
`ConnectorError.provider_status_code`. Both surface 401 and 403 distinctly and
discard provider error bodies. Probe success verifies subscription read access
for the supplied key. [Subscription endpoint](https://elevenlabs.io/docs/api-reference/user/subscription/get/)

The models action retains every provider `model_id`, the name when supplied,
the `can_do_text_to_speech`, `can_use_style` and `can_use_speaker_boost` flags,
and the provided `languages` and `maximum_text_length_per_request`. A missing
flag conveys no capability information. The returned text limit is the current
provider field. [Model endpoint](https://elevenlabs.io/docs/api-reference/models/list)

`page_size` accepts 1–100 and defaults to 10. The first page can include extra
default voices. Each response echoes the requested `page_size` and `page_token`
and includes `voices` with each `voice_id` and any supplied `name`. When
`has_more` is true, pass the returned `next_page_token` as `page_token` on the
next action call. The connector sends it as the provider's `next_page_token`
query parameter. The first request uses no token. An empty final page returns
an empty `voices` array, `has_more: false` and `next_page_token: null` when the
provider supplies no continuation. [Voices endpoint](https://elevenlabs.io/docs/api-reference/voices/search)

Both settings reads return the settings fields present in the provider reply.
Every field is optional and nullable. Explicit `null`, numeric `0` and boolean
`false` are preserved, and an absent field stays absent.

| Field | Type and published range | Applicability |
| --- | --- | --- |
| `stability` | Number, 0–1 | Controls variation between generations. |
| `similarity_boost` | Number, 0–1 | Controls similarity to the source voice. The Eleven v3 guide excludes this control. |
| `style` | Number, with no bounds specified by the settings API | Controls style exaggeration. Check `can_use_style`. Eleven v4 and v4 Turbo omit the style control. |
| `speed` | Number, supported control range 0.7–1.2 | A multiplier of 1 is normal speed. The ElevenCreative guide excludes Eleven v3, v4 and v4 Turbo from speed control. |
| `use_speaker_boost` | Boolean | Check `can_use_speaker_boost`. Eleven v3 omits speaker boost. |
| `clone_strength` | Number, 0–1 | Applies to a Professional Voice Clone fine-tuned for the selected v4 model. A returned value alone conveys no eligibility information. |

[Default settings](https://elevenlabs.io/docs/api-reference/voices/settings/get-default) ·
[Saved settings](https://elevenlabs.io/docs/api-reference/voices/settings/get) ·
[Model guidance](https://elevenlabs.io/docs/eleven-creative/playground/text-to-speech)
