# Salesforce protocol notes

Distilled from the [StackOS source review](https://github.com/flowmonkeyio/StackOS/blob/3121f4af370fbad273d08a9469d8961de2278534/docs/integration-contracts/gtm-crm.md)
during extraction on 2026-10-05. These provider references do not establish
which actions are installed; use the executable catalog for that.

Use the authenticated instance URL for REST requests. sObject upsert by
external ID can create a record; updateOnly changes that behavior and must
not be inferred from a generic update intent.

Query responses expose done and nextRecordsUrl. Continue with the provider's
query locator rather than constructing an unrelated query. Preserve
Sforce-Limit-Info and structured errors: REQUEST_LIMIT_EXCEEDED can arrive
with HTTP 403, not only 429.

Sources: [REST API Developer Guide: sObject Rows by External ID, Query and errors](https://resources.docs.salesforce.com/latest/latest/en-us/sfdc/pdf/api_rest.pdf)
and [API limits and monitoring](https://developer.salesforce.com/blogs/2024/11/api-limits-and-monitoring-your-api-usage).
