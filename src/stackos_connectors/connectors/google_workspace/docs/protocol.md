# Google Workspace protocol notes

Distilled from the [StackOS source review](https://github.com/flowmonkeyio/StackOS/blob/3121f4af370fbad273d08a9469d8961de2278534/docs/integration-contracts/gtm-prospecting-outbound.md)
during extraction on 2026-10-05. These provider references do not establish
which actions are installed; use the executable catalog for that.

Gmail quotas are measured in quota units, so request counts alone do not
describe remaining send capacity. Calendar also applies burst limits to
writes against one calendar. Calendar rate-limit errors can use 403 or 429;
preserve the provider error reason and use bounded backoff where retryable.

For delegated service-account calls, the authorized Workspace subject
selects the represented user. A target user identifier in an action does
not establish domain-wide delegation or grant mailbox access.

Sources: [Gmail usage limits](https://developers.google.com/workspace/gmail/api/reference/quota),
[Calendar quotas](https://developers.google.com/workspace/calendar/api/guides/quota),
[Calendar errors](https://developers.google.com/workspace/calendar/api/guides/errors), and
[service-account delegation](https://developers.google.com/identity/protocols/oauth2/service-account#delegatingauthority).
