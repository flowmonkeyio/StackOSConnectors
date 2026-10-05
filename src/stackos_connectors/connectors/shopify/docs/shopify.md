# Shopify Admin GraphQL protocol

Source: [reviewed source contract](https://github.com/flowmonkeyio/StackOS/blob/3121f4af370fbad273d08a9469d8961de2278534/docs/integration-contracts/shopify.md).
Condensed during extraction on 2026-10-05; provider documentation was not
reverified live. Installed actions and schemas are defined by the catalog.

The endpoint is
`POST https://{store_domain}/admin/api/{api_version}/graphql.json`,
authenticated with X-Shopify-Access-Token. The reviewed client targets version
2026-07 and myshopify.com store domains. Provider scopes and protected customer
data approval still govern which fields the token can access.

Fixed requests live in [assets/graphql](../assets/graphql/). GraphQL top-level
errors and mutation userErrors must be inspected even after HTTP 200.
Preserve userErrors.code and extensions.cost, including throttle status.
Query-complexity points are not a monetary price.

Connections have bounded pages and pageInfo; nested connection data can be
truncated independently of the outer page. Older order history can require
read_all_orders. Order reads selecting customer fields also need
read_customers; marking orders paid additionally needs the provider's
mark_orders_as_paid permission.

Provider payload caveats retained from the action review:

- ProductStatus includes UNLISTED; product search status filters are lowercase.
- orderUpdate tags replaces the tag list; tagsAdd/tagsRemove are separate
  operations. Product tag addition and removal may require two mutations.
- Collection creation uses collectionCreate(collection:).
- DraftOrder returns note2. Order lookup by name must not force an extra #.
- Inventory adjustments/set quantities use the reviewed @idempotent directive
  and changeFromQuantity input. A null comparison value intentionally skips
  the compare-and-swap check.
- Variant bulk create/update return structured userErrors, including code.
- Customer display names are not unique IDs; a report grouped by name does not
  establish identity.

Sources: [Admin GraphQL](https://shopify.dev/docs/api/admin-graphql/latest),
[scopes](https://shopify.dev/docs/api/usage/access-scopes),
[limits](https://shopify.dev/docs/api/usage/limits),
[ProductStatus](https://shopify.dev/docs/api/admin-graphql/latest/enums/ProductStatus),
[product filters](https://shopify.dev/docs/api/admin-graphql/latest/queries/products),
[orderUpdate](https://shopify.dev/docs/api/admin-graphql/latest/mutations/orderUpdate),
[inventory adjustments](https://shopify.dev/docs/api/admin-graphql/latest/mutations/inventoryAdjustQuantities),
[quantity setting](https://shopify.dev/docs/api/admin-graphql/latest/mutations/inventorySetQuantities),
[shop probe](https://shopify.dev/docs/api/admin-graphql/latest/queries/shop),
[ShopifyQL query](https://shopify.dev/docs/api/admin-graphql/latest/queries/shopifyqlQuery),
[ShopifyQL language](https://shopify.dev/docs/api/shopifyql),
[orders](https://shopify.dev/docs/api/admin-graphql/latest/queries/orders),
[variant create errors](https://shopify.dev/docs/api/admin-graphql/latest/objects/ProductVariantsBulkCreateUserError),
and [variant update errors](https://shopify.dev/docs/api/admin-graphql/latest/objects/ProductVariantsBulkUpdateUserError).
