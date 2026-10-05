# Slack protocol notes

Distilled from the [StackOS source review](https://github.com/flowmonkeyio/StackOS/blob/3121f4af370fbad273d08a9469d8961de2278534/docs/integration-contracts/communications.md)
during extraction on 2026-10-05. These provider references do not establish
which actions are installed; use the executable catalog for that.

Conversation history is paginated. Preserve has_more, next_cursor and any
provider is_limited flag; an absent limit flag does not establish complete
retention or access. A message's reply_count does not contain its thread
replies. Follow the returned cursor and honor Retry-After because effective
limits depend on the app class.

Posting requires the token's scopes and conversation membership. Threads use
thread_ts; bot-token deletion is limited to messages posted by that bot.
A file descriptor is not the downloaded file content.

Sources: [conversation history](https://docs.slack.dev/reference/methods/conversations.history/),
[thread replies](https://docs.slack.dev/reference/methods/conversations.replies/),
[file objects](https://docs.slack.dev/reference/objects/file-object/), and
[posting messages](https://docs.slack.dev/reference/methods/chat.postMessage/).
