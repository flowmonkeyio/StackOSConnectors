# Microsoft Graph protocol notes

Distilled from the [StackOS source review](https://github.com/flowmonkeyio/StackOS/blob/3121f4af370fbad273d08a9469d8961de2278534/docs/integration-contracts/gtm-prospecting-outbound.md)
during extraction on 2026-10-05. These provider references do not establish
which actions are installed; use the executable catalog for that.

sendMail returns 202 Accepted with no body. This acknowledges processing,
not final delivery. Creating a calendar event returns an event object and
supports a transaction ID for duplicate-request protection.

Preserve request-id and Retry-After when returned. A successful outer batch
HTTP response can still contain individually throttled operations; inspect
each subresponse before deciding what can be retried.

Sources: [sendMail](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0),
[event creation](https://learn.microsoft.com/en-us/graph/api/calendar-post-events?view=graph-rest-1.0),
and [throttling](https://learn.microsoft.com/en-us/graph/throttling).
