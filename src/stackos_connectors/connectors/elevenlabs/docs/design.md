# ElevenLabs Voice Design

Both actions require a resolved ElevenLabs API key with access to the requested
route. The caller selects which generated voice to create.

| Native action | ElevenLabs route | Result |
| --- | --- | --- |
| `elevenlabs.voices.design` | `POST /v1/text-to-voice/design` | Generated preview IDs and local audio files |
| `elevenlabs.voices.create` | `POST /v1/text-to-voice` | Permanent `voice_id` for the selected preview |

For design, pass a `voice_description` of 20–1,000 characters, either explicit
`text` of 100–1,000 characters or `auto_generate_text: true`, and a writable
`CallOptions(output_dir=...)`. The connector checks the directory before
dispatch. The design models are `eleven_multilingual_ttv_v2` (the provider
default) and `eleven_ttv_v3`. Both accept design controls such as `loudness`,
`seed`, `guidance_scale`, `should_enhance` and `quality`. Reference audio
(`reference_audio_base64`) and `prompt_strength` require `eleven_ttv_v3`.
Optional `output_format` is sent in the query. Previews use the non-streaming
response. [Design a voice](https://elevenlabs.io/docs/api-reference/text-to-voice/design)

`result.output_json.previews` follows provider order. Each saved preview row
contains `generated_voice_id`, `duration_secs`, `media_type` and a zero-based
`file_index` into `result.files`. Each file carries its preview's MIME type.
Recognized MIME types determine the file extension. Generic or unrecognized
types use `.bin` and retain the reported MIME type. Audio bytes are available
through the file descriptors. The connector also returns the provider's preview
`text` and per-preview `language` when supplied.

Design reports `success` when all previews are saved, `partial` when some are
saved, and `failed` when no preview is saved from a returned batch. Failed rows
retain valid provider fields, including any known generated ID, and carry
`reason_code` instead of a file index. The connector attempts subsequent
previews in the same batch after a malformed preview or write failure. A preview
with a malformed MIME type reports `invalid_preview` and keeps its known ID and
duration.

To create a permanent voice, separately pass the chosen `generated_voice_id`,
`voice_name` and `voice_description`. Each invocation sends at most one POST.
For errors after dispatch and incomplete design results, inspect the provider
receipt and any returned preview IDs before deciding whether to send a new
request. [Create a voice](https://elevenlabs.io/docs/api-reference/text-to-voice/create)
