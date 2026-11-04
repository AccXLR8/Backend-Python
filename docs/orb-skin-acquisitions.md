# Gems and skin bundle shop

Orb skins remain owned and equipped by user ID. `classic` is free and assigned to every account. Purchasable orb skins are paired with a board skin and sold as a single Gem-priced bundle. Razorpay is used only to buy Gem packs; skins are never charged directly to a payment provider.

## Achievement rewards

`achievement_orb_skin_rewards` maps achievements to orb skins. When an achievement is earned, the profile service grants mapped skins to `user_orb_skins` in the same transaction. Grants are idempotent, and profile reads reconcile rewards mapped after an achievement was earned.

Achievement entries expose `orb_skin_reward_ids`; the catalog exposes `unlock_achievement_codes`. The Gilded Glass legendary orb (stable ID `auric-comet`) is awarded by `RANKED_WIN_STREAK_20` after 20 consecutive ranked wins. Migration `20261103_000017` adds its catalog entry and reward mapping, then backfills current ranked streaks from completed ranked games. It is achievement-only: `price_gems` remains `NULL`.

Gilded Glass remains an achievement reward and is not included in the Gem shop. Earning the achievement adds the orb to the player's inventory; the player can equip it from the skin locker.

Bundled frontend assets live at `public/orb-skins/<asset-key>/`: `preview.webp`, `texture.webp`, `detail.webp`, and `collision.mp3`. Void Prism uses `prism-lattice.svg` as its detail layer to render the animated wireframe orb. Deploy frontend assets before or alongside the backend migration; the backend asset URLs use the configured `ORB_SKIN_ASSET_BASE_URL` and each skin's asset key/version.

## Reference collection

Migration `20261103_000018` seeds the 12-skin Orb Skins collection shown in the frontend locker. `classic` remains free and is assigned to every account. Migration `20261104_000019` seeds paired board/orb offers with draft Gem prices stored on `skin_bundles.price_gems`.

| Rarity | Orb skin | Bundle status |
| --- | --- | ---: |
| Legendary | Gilded Glass | 20 consecutive ranked wins; not purchasable |
| Legendary | Cosmic Core | Paired with Void Matrix |
| Epic | Crimson Glass | Paired with Crimson Grid |
| Epic | Void Prism | Paired with Void Matrix |
| Epic | Neon Circuit | Paired with Neon Circuit |
| Rare | Cool Blue Glass | Paired with Ocean Grid |
| Rare | Frosted Core | Paired with Frost Grid |
| Rare | Electric Halo | Paired with Solar Grid |
| Common | Classic Orb | Free; included in every inventory |
| Common | Matte Pearl | Paired with Carbon Grid |
| Common | Graphite | Paired with Carbon Grid |
| Common | Ivory Glow | Paired with Solar Grid |

Only Classic is granted by default. The shop catalog lists board/orb bundles; a single purchase deducts the bundle price and grants both items. Gilded Glass stays linked to the ranked streak reward and has no Gem price.

New visual assets are bundled by asset key under the frontend's `public/orb-skins/` directory. The default asset base is `https://superkritical.com`; set `ORB_SKIN_ASSET_BASE_URL` on the backend if production uses a different asset host. Until distinct collision sounds are commissioned, each new skin bundles the Classic collision clip. Replace the file for a skin and increment its `asset_version` to publish a distinct sound without changing the API contract.

Migration `20261104_000021` advances the Void Prism and Circuit Pulse asset versions to `2` and updates the Void Matrix board description. The Void Prism detail URL resolves to `prism-lattice.svg`.

## Configure products

Set `skin_bundles.price_gems` to the server-authoritative price for a complete bundle. Bundle rarity is `common`, `rare`, `epic`, or `legendary`.

The old direct-cash columns and purchase ledger are retained as legacy history, but the shop never reads them. Existing cash prices are not converted to Gems automatically; set `price_gems` explicitly for each skin you want to sell. The Gilded Glass reward is not assigned a price; Gem packs must also be seeded separately before players can buy Gems.

```sql
UPDATE skin_bundles SET price_gems = <gem-price>, rarity = 'rare'
WHERE id = 'replace-with-bundle-id';
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
- `GET /api/shop/bundles` returns enabled board/orb pairs and their single Gem price.
- `GET /api/shop/wallet` returns `{ "currency": "gems", "display_name": "Gems", "balance": 0 }`.
- `POST /api/shop/gem-packs/{pack_id}/checkout` creates a Razorpay order for the selected Gem pack using its stored backend price.
- `POST /api/shop/gem-packs/verify` verifies the signed checkout result, checks the payment is captured and matches the stored order, then credits Gems exactly once.
- `POST /api/shop/bundles/{bundle_id}/purchase` atomically deducts the bundle's Gem price and grants both its board and orb skin.

The verification body is:

```json
{
  "order_id": "order_...",
  "payment_id": "pay_...",
  "signature": "..."
}
```

The bundle purchase response includes `gems_spent`, `gem_balance`, `owned_orb_skin_ids`, and `owned_board_skin_ids`. Existing single-orb purchases are retained as a compatibility route, but catalog orb prices are cleared so they cannot be used for new standalone sales. Items are not equipped automatically; use `PATCH /api/profiles/me` with the relevant equipped skin field after they appear in the player's inventory.

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
