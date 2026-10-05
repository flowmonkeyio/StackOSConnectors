# Telegram TDLib protocol notes

Distilled from the [StackOS source review](https://github.com/flowmonkeyio/StackOS/blob/3121f4af370fbad273d08a9469d8961de2278534/docs/integration-contracts/communications.md)
during extraction on 2026-10-05. These provider references do not establish
which actions are installed; use the executable catalog for that.

A pending TDLib send is not a final delivery receipt. Preserve the native
message identity and correlate later success or failure updates before
declaring the send complete or deciding whether a retry is safe.

Chat-list loading, chat-list reads and chat-history reads are distinct native
operations. Available history depends on the account kind and access;
Telegram's underlying dialogs and history methods are user-only.
Keep native account/session authorization separate from message payloads.

Sources: [TDLib API](https://core.telegram.org/tdlib/docs/td__api_8h.html),
[loadChats](https://core.telegram.org/tdlib/docs/classtd_1_1td__api_1_1load_chats.html),
[getChatHistory](https://core.telegram.org/tdlib/docs/classtd_1_1td__api_1_1get_chat_history.html),
[dialogs](https://core.telegram.org/method/messages.getDialogs), and
[history](https://core.telegram.org/method/messages.getHistory).
