# Board and orb skin bundles

Shop purchases are now curated bundles containing one board skin and one orb skin. Gem prices belong to the bundle record and are validated by the backend. The legacy single-orb purchase route remains for compatibility, but catalog orb prices are cleared so no skin is sold on its own.

## Catalog

`GET /api/shop/bundles` returns enabled products, each with `id`, `name`, `rarity`, `price_gems`, a `board_skin` object, and an `orb_skin` object (including its existing asset URLs and collision sound). Draft product pricing is seeded in migration `20261104_000019` and can be adjusted on `skin_bundles.price_gems`.

## Purchase

`POST /api/shop/bundles/{bundle_id}/purchase` requires a registered user and deducts the bundle's Gem price in the same transaction that grants both inventory items. The response includes updated Gem balance and both `owned_orb_skin_ids` and `owned_board_skin_ids`. Repeated purchases are rejected, and the wallet row lock prevents concurrent clicks from charging twice.

## Inventory and selection

`GET /api/profiles/me` includes `owned_board_skin_ids` and `equipped_board_skin_id`. `PATCH /api/profiles/me` accepts `equipped_board_skin_id` and only permits a skin that is in that user's board inventory. `GET /api/skins/boards` returns the board-skin catalog.

Every account starts with `classic-grid`. Existing owners of an orb that is included in a bundle are granted its paired board during the migration, preserving their existing purchase. The ranked 20-win Gilded Glass reward remains achievement-only.
