# ElevenLabs pronunciation dictionaries

`elevenlabs.dictionaries.create` sends `POST /v1/pronunciation-dictionaries/add-from-rules`
with a resolved ElevenLabs API key. Supply a nonblank `name` and one or more
rules in the desired order.

- An alias rule has `type: "alias"`, `string_to_replace` and `alias`.
- A phoneme rule has `type: "phoneme"`, `string_to_replace`, `phoneme` and
  `alphabet`, such as `"ipa"`.

Both variants accept optional boolean `case_sensitive` and `word_boundaries`.
ElevenLabs applies its defaults when the flags are omitted.
[Create a dictionary from rules](https://elevenlabs.io/docs/api-reference/pronunciation-dictionaries/create-from-rules)

`result.output_json` contains the provider's `id` and `version_id`.
To use that specific version with `elevenlabs.speech.generate`, set
`pronunciation_dictionary_id` to the returned `id` and `version_id` to the
returned `version_id` in an entry in `pronunciation_dictionary_locators`.
[Create speech with timing](https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps)

Each invocation sends at most one POST. On a failure after dispatch, inspect
the provider receipt and `outcome_unknown` before submitting another request.
