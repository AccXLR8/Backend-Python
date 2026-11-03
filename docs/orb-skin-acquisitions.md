# Gems and orb skin shop

Orb skins remain owned and equipped by user ID. `classic` is free and assigned to every account. Players can acquire other skins through achievement rewards or by spending Gems in the shop. Razorpay is used only to buy Gem packs; a skin is never charged directly to a payment provider.

## Achievement rewards

`achievement_orb_skin_rewards` maps achievements to orb skins. When an achievement is earned, the profile service grants mapped skins to `user_orb_skins` in the same transaction. Grants are idempotent, and profile reads reconcile rewards mapped after an achievement was earned.

Achievement entries expose `orb_skin_reward_ids`; the catalog exposes `unlock_achievement_codes`. The Gilded Glass legendary orb (stable ID `auric-comet`) is awarded by `RANKED_WIN_STREAK_20` after 20 consecutive ranked wins. Migration `20261103_000017` adds its catalog entry and reward mapping, then backfills current ranked streaks from completed ranked games. It is achievement-only: `price_gems` remains `NULL`.

The frontend shop lists achievement rewards as locked items with their unlock requirement. They are never purchasable with Gems. Earning the achievement adds the skin to the player's inventory; the player can then equip it from the skin locker.

Bundled frontend assets live at `public/orb-skins/<asset-key>/`: `preview.webp`, `texture.webp`, `detail.webp`, and `collision.mp3`. Deploy the frontend assets before or alongside the backend migration. The backend asset URLs use the configured `ORB_SKIN_ASSET_BASE_URL` and each skin's asset key/version.

## Reference collection

Migration `20261103_000018` seeds the 12-skin Orb Skins collection shown in the frontend locker. `classic` remains free and is assigned to every account. Gem prices below are draft balancing values and can be changed by updating `orb_skins.price_gems`.

| Rarity | Skin | Draft price / unlock |
| --- | --- | ---: |
| Legendary | Gilded Glass | 20 consecutive ranked wins; not purchasable |
| Legendary | Cosmic Core | 4,500 Gems |
| Epic | Crimson Glass | 2,500 Gems |
| Epic | Void Prism | 2,500 Gems |
| Epic | Neon Circuit | 2,500 Gems |
| Rare | Cool Blue Glass | 1,200 Gems |
| Rare | Frosted Core | 1,200 Gems |
| Rare | Electric Halo | 1,200 Gems |
| Common | Classic Orb | Free; included in every inventory |
| Common | Matte Pearl | 500 Gems |
| Common | Graphite | 500 Gems |
| Common | Ivory Glow | 500 Gems |

Only Classic is granted by default. The shop catalog marks priced skins for sale but does not add them to inventory; the existing server-side purchase endpoint deducts Gems and grants ownership. Gilded Glass stays linked to the ranked streak reward and has no Gem price.

New visual assets are bundled by asset key under the frontend's `public/orb-skins/` directory. The default asset base is `https://superkritical.com`; set `ORB_SKIN_ASSET_BASE_URL` on the backend if production uses a different asset host. Until distinct collision sounds are commissioned, each new skin bundles the Classic collision clip. Replace the file for a skin and increment its `asset_version` to publish a distinct sound without changing the API contract.

## Configure products

Set `orb_skins.price_gems` to the server-authoritative price for a skin. Leave it `NULL` when the skin is not for sale. Rarity is `common`, `rare`, `epic`, or `legendary`.

The old direct-cash columns and purchase ledger are retained as legacy history, but the shop never reads them. Existing cash prices are not converted to Gems automatically; set `price_gems` explicitly for each skin you want to sell. The Gilded Glass reward is not assigned a price; Gem packs must also be seeded separately before players can buy Gems.

```sql
UPDATE orb_skins
SET price_gems = <gem-price>, rarity = 'rare'
WHERE id = 'replace-with-skin-id';
```

Gem pack prices and amounts live in `gem_packs`. Create the offers approved for the game using backend operator tooling; the frontend reads them from the API and does not define pack amounts or prices.

```sql
INSERT INTO gem_packs (id, name, gems_amount, price_amount_minor, price_currency, enabled, sort_order)
VALUES ('replace-with-pack-id', 'replace-with-pack-name', <gem-amount>, <price-in-minor-units>, 'INR', true, 0);
```

`price_amount_minor` uses Razorpay's currency subunits. For INR, one rupee is 100 paise. Do not put real prices in frontend configuration.

## Shop API

All wallet and purchase routes require a registered account. Guests can browse products and play, but need an account to buy Gems or skins.

- `GET /api/shop/gem-packs` returns enabled Gem pack offers.
- `GET /api/shop/wallet` returns `{ "currency": "gems", "display_name": "Gems", "balance": 0 }`.
- `POST /api/shop/gem-packs/{pack_id}/checkout` creates a Razorpay order for the selected Gem pack using its stored backend price.
- `POST /api/shop/gem-packs/verify` verifies the signed checkout result, checks the payment is captured and matches the stored order, then credits Gems exactly once.
- `POST /api/shop/orb-skins/{skin_id}/purchase` atomically deducts the skin's Gem price and adds it to the player's existing skin inventory.

The verification body is:

```json
{
  "order_id": "order_...",
  "payment_id": "pay_...",
  "signature": "..."
}
```

The skin purchase response includes `gems_spent`, `gem_balance`, and `owned_orb_skin_ids`. Purchase retries cannot double-spend or grant duplicate inventory entries. The skin does not become equipped automatically; use `PATCH /api/profiles/me` with `equipped_orb_skin_id` after it appears in the player's inventory.

## Razorpay webhook

Configure Razorpay to send `payment.captured` events to:

```text
https://<backend-host>/api/shop/razorpay/webhook
```

The endpoint validates `X-Razorpay-Signature` against the raw request body using `RAZORPAY_WEBHOOK_SECRET`. It credits only a captured payment that matches a known Gem pack order. Duplicate webhook deliveries are safe. Configure automatic capture; an authorized-only payment does not credit Gems.

## Backend environment

Set these on the backend deployment only:

- `RAZORPAY_KEY_ID` — public key ID returned to hosted Checkout.
- `RAZORPAY_KEY_SECRET` — used only for server-to-server API calls and signature verification.
- `RAZORPAY_WEBHOOK_SECRET` — the separate secret configured for the webhook endpoint.

Use Razorpay test keys while validating, then replace them with live keys. Never expose either secret through a `NEXT_PUBLIC_*` frontend variable.
