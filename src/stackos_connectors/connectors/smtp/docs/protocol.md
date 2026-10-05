# SMTP protocol notes

Distilled from the [StackOS source review](https://github.com/flowmonkeyio/StackOS/blob/3121f4af370fbad273d08a9469d8961de2278534/docs/integration-contracts/communications.md)
during extraction on 2026-10-05. These provider references do not establish
which actions are installed; use the executable catalog for that.

SMTP server acceptance means acceptance for relay, not confirmed delivery,
inbox placement, opening or reading. Recipient acceptance can be partial;
preserve individual rejections instead of reporting universal success.
Transport failure after message submission may leave the outcome uncertain.

Sources: [SMTP, including relay responsibility](https://www.rfc-editor.org/rfc/rfc5321.html)
and [SMTP AUTH](https://www.rfc-editor.org/rfc/rfc4954).
