# Orb skin API examples

The machine-readable HTTP contract is [OpenAPI](api/openapi.json). WebSocket snapshots use the player shape shown below. Missing `orb_skin_id` in data written by older server versions means `classic`.

## `GET /api/profiles/me`

```json
{
  "user_id": "00000000-0000-0000-0000-000000000001",
  "username": "PlayerOne",
  "avatar": { "id": 1, "code": "green-reactor", "name": "Green Reactor", "image_url": "/avatars/green-reactor.svg" },
  "stats": { "matches_played": 0, "matches_won": 0, "win_rate": 0, "current_win_streak": 0, "longest_win_streak": 0, "longest_chain": 0 },
  "achievements": [],
  "created_at": "2026-10-28T00:00:00Z",
  "profile_age_days": 0,
  "equipped_orb_skin_id": "classic",
  "owned_orb_skin_ids": ["classic"]
}
```

## `GET /api/skins/orbs`

```json
{
  "skins": [
    { "id": "classic", "name": "Classic Orb", "asset_key": "classic", "preview_asset_key": "classic", "enabled": true }
  ]
}
```

## `GET /api/games/{id}`

```json
{
  "id": "00000000-0000-0000-0000-000000000010",
  "status": "ACTIVE",
  "players": [
    { "user_id": "00000000-0000-0000-0000-000000000001", "username": "PlayerOne", "player_slot": 0, "connected": true, "time_remaining_ms": 300000, "orb_skin_id": "classic" },
    { "user_id": "00000000-0000-0000-0000-000000000002", "username": "Guest42", "player_slot": 1, "connected": true, "time_remaining_ms": 300000, "orb_skin_id": "classic" }
  ],
  "turn_number": 0,
  "current_player_slot": 0,
  "created_at": "2026-10-28T00:00:00Z",
  "updated_at": "2026-10-28T00:00:00Z"
}
```

## WebSocket `state_snapshot`

```json
{
  "type": "state_snapshot",
  "payload": {
    "game_id": "00000000-0000-0000-0000-000000000010",
    "status": "ACTIVE",
    "turn_number": 0,
    "current_player_slot": 0,
    "state": { "board": [] },
    "players": [
      { "user_id": "00000000-0000-0000-0000-000000000001", "username": "PlayerOne", "player_slot": 0, "connected": true, "time_remaining_ms": 300000, "orb_skin_id": "classic" },
      { "user_id": "00000000-0000-0000-0000-000000000002", "username": "Guest42", "player_slot": 1, "connected": true, "time_remaining_ms": 300000, "orb_skin_id": "classic" }
    ]
  }
}
```
