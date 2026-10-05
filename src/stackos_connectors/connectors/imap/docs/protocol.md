# IMAP protocol notes

Distilled from the [StackOS source review](https://github.com/flowmonkeyio/StackOS/blob/3121f4af370fbad273d08a9469d8961de2278534/docs/integration-contracts/communications.md)
during extraction on 2026-10-05. These provider references do not establish
which actions are installed; use the executable catalog for that.

Use message UIDs together with the mailbox's UIDVALIDITY. Sequence numbers
can change, and a changed UIDVALIDITY invalidates a saved continuation.
Read-only selection and BODY.PEEK reads avoid marking messages as seen.

UID continuation is exclusive. RFC 9051's UID n:* range can include the final
UID even when n exceeds it; filter returned UIDs against the requested bound
to avoid repeating the final page. Search counts describe a changing mailbox,
not an immutable snapshot. A bounded MIME-prefix fetch does not establish
complete message or attachment content.

Sources: [UID semantics](https://www.rfc-editor.org/rfc/rfc9051.html#section-6.4.9),
[FETCH and BODY.PEEK](https://www.rfc-editor.org/rfc/rfc9051.html#section-6.4.5),
and [Python IMAP read-only selection](https://docs.python.org/3/library/imaplib.html).
