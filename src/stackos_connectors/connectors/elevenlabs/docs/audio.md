# ElevenLabs speech and forced alignment

Both native actions require a resolved ElevenLabs API key with access to the
requested endpoint. The provider may charge for these operations.

| Native action | ElevenLabs route | Result |
| --- | --- | --- |
| `elevenlabs.speech.generate` | `POST /v1/text-to-speech/{voice_id}/with-timestamps` | One audio file and any supplied character alignments |
| `elevenlabs.alignment.create` | `POST /v1/forced-alignment` | Provider word timings and any supplied character timings and loss |

For speech, pass `voice_id`, `text` and a writable `CallOptions(output_dir=...)`. Optional
body fields include `model_id`, `voice_settings`, `previous_text`, `next_text`,
`seed` (0 through 4294967295) and up to three ordered
`pronunciation_dictionary_locators`. Each locator takes a
`pronunciation_dictionary_id` and an optional `version_id`. To pin a
[created dictionary version](dictionary.md), use its returned `id` as
`pronunciation_dictionary_id` and its returned `version_id` as `version_id`.
Omitting the version or passing null selects the provider's latest version. An
explicitly selected `output_format` goes in the query. The provider default is
`mp3_44100_128`.

The connector creates the requested output directory if needed and verifies
that it can write a temporary file there before sending the request. The
temporary file is removed before dispatch. A newly created directory may
remain empty if the provider request fails.

The connector returns the decoded audio in `result.files[0]` with the format's
extension and MIME type. `result.output_json` reports the effective
`output_format` and preserves each supplied `alignment` and
`normalized_alignment` independently, including explicit nulls. Omitted
alignments stay absent. The file descriptor contains the local path.
[Create speech with timing](https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps)

For forced alignment, pass a readable local `audio_path` under 1 GB, its
`mime_type` (such as `audio/wav`) and the transcript as `text`. The connector
uploads the file and transcript as separate multipart fields. Word and
character records retain provider `text`, `start` and `end` in seconds.
Word-level and response-level `loss` values are preserved when supplied.
[Create forced alignment](https://elevenlabs.io/docs/api-reference/forced-alignment/create)

Each invocation sends at most one POST. A 429 records dispatch separately from
possible charge and retains any retry delay. Timeouts, server errors and malformed
successes can leave the provider outcome and charge uncertain. Any safe IDs
already received in the headers remain available if reading the body times out.
When generation succeeds and saving fails, the error reports provider execution
and possible charge. Failures
after dispatch carry `retry_safe: false` and retain safe request or history
IDs when available. Inspect these details before choosing whether to submit
a new request. [ElevenLabs errors](https://elevenlabs.io/docs/eleven-api/resources/errors)
