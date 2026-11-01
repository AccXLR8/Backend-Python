from __future__ import annotations

import time
import uuid
from typing import Union

from redis.asyncio import Redis

from app.config.settings import get_settings
from app.matchmaking.modes import MatchmakingMode
from app.redis.keys import (
    matchmaking_assignment_key,
    matchmaking_claim_key,
    matchmaking_owner_key,
    matchmaking_queue_key,
    matchmaking_ranked_flux_key,
    matchmaking_ranked_queue_key,
)


# All queue switching and pair claiming is done in one Redis operation. The
# shared owner and claim keys make a user mutually exclusive across every mode.
_JOIN_AND_MATCH_SCRIPT = """
local user = ARGV[1]
local mode = ARGV[2]
local flux = ARGV[3]
local now = tonumber(ARGV[4])
local claim = ARGV[5]
local claim_ttl = tonumber(ARGV[6])
local queue_ttl = tonumber(ARGV[7])

local assignment = redis.call('GET', KEYS[5])
if assignment then return {'assigned', assignment} end
if redis.call('GET', KEYS[6]) then return {'claimed'} end

-- Switching modes removes all prior memberships. Rejoining the current mode
-- keeps the original queue time so retries do not reset ranked wait expansion.
local previous_mode = redis.call('GET', KEYS[7])
if mode == 'ranked' and previous_mode == mode and redis.call('ZSCORE', KEYS[3], user) then
  redis.call('SREM', KEYS[1], user)
  redis.call('SREM', KEYS[2], user)
  redis.call('HSET', KEYS[4], user, flux)
elseif mode ~= 'ranked' and previous_mode == mode then
  local queue = mode == 'mini' and KEYS[2] or KEYS[1]
  redis.call('SREM', mode == 'mini' and KEYS[1] or KEYS[2], user)
  redis.call('ZREM', KEYS[3], user)
  redis.call('HDEL', KEYS[4], user)
  redis.call('SADD', queue, user)
else
  redis.call('SREM', KEYS[1], user)
  redis.call('SREM', KEYS[2], user)
  redis.call('ZREM', KEYS[3], user)
  redis.call('HDEL', KEYS[4], user)
  if mode == 'ranked' then
    redis.call('ZADD', KEYS[3], now, user)
    redis.call('HSET', KEYS[4], user, flux)
  else
    local queue = mode == 'mini' and KEYS[2] or KEYS[1]
    redis.call('SADD', queue, user)
  end
end
redis.call('SET', KEYS[7], mode, 'EX', queue_ttl)

local first = nil
local second = nil
if mode == 'ranked' then
  -- Bound Lua work under large queues; oldest waiters are considered first.
  local members = redis.call('ZRANGE', KEYS[3], 0, 199, 'WITHSCORES')
  local best = nil
  local best_distance = 1000000
  local best_time = 1000000000000000
  local user_flux = flux == 'unranked' and nil or tonumber(flux)
  local user_queued = tonumber(redis.call('ZSCORE', KEYS[3], user))
  for i = 1, #members, 2 do
    local candidate = members[i]
    local candidate_time = tonumber(members[i + 1])
    if redis.call('GET', ARGV[8] .. candidate) ~= 'ranked'
      or redis.call('GET', ARGV[9] .. candidate)
      or redis.call('GET', ARGV[10] .. candidate) then
      redis.call('ZREM', KEYS[3], candidate)
      redis.call('HDEL', KEYS[4], candidate)
    elseif candidate ~= user then
      local other_text = redis.call('HGET', KEYS[4], candidate) or 'unranked'
      local other_flux = other_text == 'unranked' and nil or tonumber(other_text)
      local allowed = false
      local distance = 0
      if user_flux == nil and other_flux == nil then
        allowed = true
      elseif user_flux ~= nil and other_flux ~= nil then
        distance = math.abs(user_flux - other_flux)
        local wait_ms = math.max(0, now - math.min(user_queued, candidate_time))
        local range = math.min(5.0, 0.5 + math.floor(wait_ms / 15000) * 0.5)
        allowed = distance <= range
      end
      if allowed and (distance < best_distance or (distance == best_distance and candidate_time < best_time)) then
        best = candidate
        best_distance = distance
        best_time = candidate_time
      end
    end
  end
  if best then
    first = user
    second = best
    redis.call('ZREM', KEYS[3], first, second)
    -- Retain flux until the claim is either assigned or safely requeued.
  end
else
  local queue = mode == 'mini' and KEYS[2] or KEYS[1]
  local eligible = {}
  local inspected = 0
  while redis.call('SCARD', queue) > 0 and #eligible < 2 and inspected < 200 do
    local candidate = redis.call('SPOP', queue)
    if candidate then
      inspected = inspected + 1
      if redis.call('GET', ARGV[8] .. candidate) == mode
        and not redis.call('GET', ARGV[9] .. candidate)
        and not redis.call('GET', ARGV[10] .. candidate) then
        table.insert(eligible, candidate)
      end
    end
  end
  if #eligible < 2 then
    for _, candidate in ipairs(eligible) do redis.call('SADD', queue, candidate) end
  else
    first = eligible[1]
    second = eligible[2]
  end
end

if not first then return {'queued'} end
for _, player in ipairs({first, second}) do
  redis.call('SET', ARGV[9] .. player, claim, 'EX', claim_ttl)
  redis.call('SET', ARGV[8] .. player, 'claimed|' .. mode)
end
return {'paired', first, second, claim}
"""

_LEAVE_SCRIPT = """
local assignment = redis.call('GET', KEYS[4])
if assignment then
  local prefix = ARGV[2] .. '|'
  if string.sub(assignment, 1, string.len(prefix)) == prefix then
    return {'matched', string.sub(assignment, string.len(prefix) + 1)}
  end
  return {'left'}
end
local claim = redis.call('GET', KEYS[5])
if claim then
  local owner = redis.call('GET', KEYS[6]) or ''
  if owner == 'claimed|' .. ARGV[2] then return {'claimed', claim} end
  return {'left'}
end
if redis.call('GET', KEYS[6]) == 'claimed|' .. ARGV[2] then
  redis.call('DEL', KEYS[6])
  return {'left'}
end
if redis.call('GET', KEYS[6]) == ARGV[2] then
  if ARGV[2] == 'ranked' then
    redis.call('ZREM', KEYS[3], ARGV[1])
    redis.call('HDEL', KEYS[2], ARGV[1])
  else
    redis.call('SREM', KEYS[1], ARGV[1])
  end
  redis.call('DEL', KEYS[6])
else
  -- Clear stale membership in this mode only. A different owner's queue state
  -- remains untouched.
  if ARGV[2] == 'ranked' then
    redis.call('ZREM', KEYS[3], ARGV[1])
    redis.call('HDEL', KEYS[2], ARGV[1])
  else
    redis.call('SREM', KEYS[1], ARGV[1])
  end
end
return {'left'}
"""

_REPAIR_EXPIRED_CLAIM_SCRIPT = """
if redis.call('GET', KEYS[5]) or redis.call('GET', KEYS[6]) then return 0 end
if redis.call('GET', KEYS[7]) ~= 'claimed|' .. ARGV[2] then return 0 end
local user = ARGV[1]
local mode = ARGV[2]
if mode == 'ranked' then
  if not redis.call('HGET', KEYS[4], user) then redis.call('HSET', KEYS[4], user, 'unranked') end
  redis.call('ZADD', KEYS[3], 'NX', tonumber(ARGV[3]), user)
else
  local queue = mode == 'mini' and KEYS[2] or KEYS[1]
  redis.call('SADD', queue, user)
end
redis.call('SET', KEYS[7], mode, 'EX', ARGV[4])
return 1
"""

_ASSIGN_PAIR_SCRIPT = """
for i = 1, 2 do
  if redis.call('GET', KEYS[2 + (i - 1) * 3]) ~= ARGV[4] then return 0 end
end
for i = 1, 2 do
  redis.call('SET', KEYS[1 + (i - 1) * 3], ARGV[1] .. '|' .. ARGV[2], 'EX', ARGV[3])
  redis.call('DEL', KEYS[2 + (i - 1) * 3])
  redis.call('SET', KEYS[3 + (i - 1) * 3], 'assigned|' .. ARGV[1], 'EX', ARGV[3])
end
if ARGV[1] == 'ranked' then
  redis.call('HDEL', KEYS[7], ARGV[5], ARGV[6])
end
return 1
"""

_RELEASE_PAIR_SCRIPT = """
local mode = ARGV[1]
local claim = ARGV[2]
local now = tonumber(ARGV[3])
local first = ARGV[4]
local second = ARGV[5]
local blocked_first = ARGV[8] == '1'
local blocked_second = ARGV[9] == '1'
if redis.call('GET', KEYS[5]) or redis.call('GET', KEYS[6]) then return 0 end
if redis.call('GET', KEYS[3]) ~= claim or redis.call('GET', KEYS[4]) ~= claim then return 0 end
redis.call('DEL', ARGV[6] .. first, ARGV[6] .. second)
if blocked_first then redis.call('SET', ARGV[7] .. first, 'busy') else redis.call('SET', ARGV[7] .. first, mode, 'EX', ARGV[10]) end
if blocked_second then redis.call('SET', ARGV[7] .. second, 'busy') else redis.call('SET', ARGV[7] .. second, mode, 'EX', ARGV[10]) end
if mode == 'ranked' then
  if not blocked_first then redis.call('ZADD', KEYS[1], now, first) end
  if not blocked_second then redis.call('ZADD', KEYS[1], now, second) end
else
  if not blocked_first then redis.call('SADD', KEYS[1], first) end
  if not blocked_second then redis.call('SADD', KEYS[1], second) end
end
redis.call('DEL', KEYS[3], KEYS[4])
return 1
"""

_REFRESH_QUEUE_LEASE_SCRIPT = """
local user = ARGV[1]
local mode = ARGV[2]
if redis.call('GET', KEYS[1]) ~= mode then return 0 end
if redis.call('GET', KEYS[2]) or redis.call('GET', KEYS[3]) then return 0 end
local queued = false
if mode == 'ranked' then
  queued = redis.call('ZSCORE', KEYS[6], user) ~= false
elseif mode == 'mini' then
  queued = redis.call('SISMEMBER', KEYS[5], user) == 1
else
  queued = redis.call('SISMEMBER', KEYS[4], user) == 1
end
if not queued then return 0 end
redis.call('EXPIRE', KEYS[1], ARGV[3])
return 1
"""


class MatchmakingQueue:
    def __init__(self, redis: Redis, mode: Union[str, MatchmakingMode] = MatchmakingMode.STANDARD):
        self.redis = redis
        self.mode = mode.value if isinstance(mode, MatchmakingMode) else mode

    async def join_and_match(self, user_id: uuid.UUID) -> tuple[str, str, str] | None:
        result = await self.redis.eval(
            _JOIN_AND_MATCH_SCRIPT,
            7,
            matchmaking_queue_key(MatchmakingMode.STANDARD.value),
            matchmaking_queue_key(MatchmakingMode.MINI.value),
            matchmaking_ranked_queue_key(),
            matchmaking_ranked_flux_key(),
            matchmaking_assignment_key(str(user_id)),
            matchmaking_claim_key(str(user_id)),
            matchmaking_owner_key(str(user_id)),
            str(user_id),
            self.mode,
            "unranked",
            int(time.time() * 1000),
            str(uuid.uuid4()),
            60,
            get_settings().matchmaking_queue_lease_seconds,
            "matchmaking:{state}:user-queue:",
            "matchmaking:{state}:claim:",
            "matchmaking:{state}:assignment:",
        )
        if self._decode(result[0]) != "paired":
            return None
        return self._decode(result[1]), self._decode(result[2]), self._decode(result[3])

    async def leave_or_get_claim(self, user_id: uuid.UUID) -> tuple[str, str | None]:
        return await self.leave_state(user_id)

    async def leave_state(self, user_id: uuid.UUID) -> tuple[str, str | None]:
        user_key = str(user_id)
        result = await self.redis.eval(
            _LEAVE_SCRIPT,
            6,
            matchmaking_queue_key(self.mode),
            matchmaking_ranked_flux_key(),
            matchmaking_ranked_queue_key(),
            matchmaking_assignment_key(user_key),
            matchmaking_claim_key(user_key),
            matchmaking_owner_key(user_key),
            user_key,
            self.mode,
        )
        return self._decode(result[0]), self._decode(result[1]) if len(result) > 1 else None

    async def repair_expired_claim(self, user_id: uuid.UUID) -> bool:
        return bool(await self.redis.eval(
            _REPAIR_EXPIRED_CLAIM_SCRIPT,
            7,
            matchmaking_queue_key(MatchmakingMode.STANDARD.value),
            matchmaking_queue_key(MatchmakingMode.MINI.value),
            matchmaking_ranked_queue_key(),
            matchmaking_ranked_flux_key(),
            matchmaking_assignment_key(str(user_id)),
            matchmaking_claim_key(str(user_id)),
            matchmaking_owner_key(str(user_id)),
            str(user_id),
            self.mode,
            int(time.time() * 1000),
            get_settings().matchmaking_queue_lease_seconds,
        ))

    async def refresh_queue_lease(self, user_id: uuid.UUID) -> bool:
        return bool(await self.redis.eval(
            _REFRESH_QUEUE_LEASE_SCRIPT,
            6,
            matchmaking_owner_key(str(user_id)),
            matchmaking_assignment_key(str(user_id)),
            matchmaking_claim_key(str(user_id)),
            matchmaking_queue_key(MatchmakingMode.STANDARD.value),
            matchmaking_queue_key(MatchmakingMode.MINI.value),
            matchmaking_ranked_queue_key(),
            str(user_id),
            self.mode,
            get_settings().matchmaking_queue_lease_seconds,
        ))

    @staticmethod
    def _decode(value) -> str:
        return value.decode() if isinstance(value, bytes) else str(value)


class RankedMatchmakingQueue:
    """Flux-aware queue for registered users playing the ranked 8x8 ruleset."""

    def __init__(self, redis: Redis):
        self.redis = redis

    async def join_and_match(self, user_id: uuid.UUID, flux: float | None) -> tuple[str, str, str] | None:
        result = await self.redis.eval(
            _JOIN_AND_MATCH_SCRIPT,
            7,
            matchmaking_queue_key(MatchmakingMode.STANDARD.value),
            matchmaking_queue_key(MatchmakingMode.MINI.value),
            matchmaking_ranked_queue_key(),
            matchmaking_ranked_flux_key(),
            matchmaking_assignment_key(str(user_id)),
            matchmaking_claim_key(str(user_id)),
            matchmaking_owner_key(str(user_id)),
            str(user_id),
            "ranked",
            f"{flux:.2f}" if flux is not None else "unranked",
            int(time.time() * 1000),
            str(uuid.uuid4()),
            60,
            get_settings().matchmaking_queue_lease_seconds,
            "matchmaking:{state}:user-queue:",
            "matchmaking:{state}:claim:",
            "matchmaking:{state}:assignment:",
        )
        if self._decode(result[0]) != "paired":
            return None
        return self._decode(result[1]), self._decode(result[2]), self._decode(result[3])

    async def leave_or_get_state(self, user_id: uuid.UUID) -> tuple[str, str | None]:
        user_key = str(user_id)
        result = await self.redis.eval(
            _LEAVE_SCRIPT,
            6,
            matchmaking_ranked_queue_key(),
            matchmaking_ranked_flux_key(),
            matchmaking_ranked_queue_key(),
            matchmaking_assignment_key(user_key),
            matchmaking_claim_key(user_key),
            matchmaking_owner_key(user_key),
            user_key,
            "ranked",
        )
        return self._decode(result[0]), self._decode(result[1]) if len(result) > 1 else None

    async def release_pair(self, pair: tuple[str, str], claim: str) -> bool:
        first, second = pair
        return bool(await self.redis.eval(
            _RELEASE_PAIR_SCRIPT,
            6,
            matchmaking_ranked_queue_key(),
            matchmaking_ranked_flux_key(),
            matchmaking_claim_key(first),
            matchmaking_claim_key(second),
            matchmaking_assignment_key(first),
            matchmaking_assignment_key(second),
            "ranked",
            claim,
            int(time.time() * 1000),
            first,
            second,
            "matchmaking:{state}:claim:",
            "matchmaking:{state}:user-queue:",
            "0",
            "0",
            get_settings().matchmaking_queue_lease_seconds,
        ))

    async def is_queued(self, user_id: uuid.UUID) -> bool:
        return bool(await self.redis.zscore(matchmaking_ranked_queue_key(), str(user_id)) is not None)

    async def repair_expired_claim(self, user_id: uuid.UUID) -> bool:
        return bool(await self.redis.eval(
            _REPAIR_EXPIRED_CLAIM_SCRIPT,
            7,
            matchmaking_queue_key(MatchmakingMode.STANDARD.value),
            matchmaking_queue_key(MatchmakingMode.MINI.value),
            matchmaking_ranked_queue_key(),
            matchmaking_ranked_flux_key(),
            matchmaking_assignment_key(str(user_id)),
            matchmaking_claim_key(str(user_id)),
            matchmaking_owner_key(str(user_id)),
            str(user_id),
            "ranked",
            int(time.time() * 1000),
            get_settings().matchmaking_queue_lease_seconds,
        ))

    async def refresh_queue_lease(self, user_id: uuid.UUID) -> bool:
        return bool(await self.redis.eval(
            _REFRESH_QUEUE_LEASE_SCRIPT,
            6,
            matchmaking_owner_key(str(user_id)),
            matchmaking_assignment_key(str(user_id)),
            matchmaking_claim_key(str(user_id)),
            matchmaking_queue_key(MatchmakingMode.STANDARD.value),
            matchmaking_queue_key(MatchmakingMode.MINI.value),
            matchmaking_ranked_queue_key(),
            str(user_id),
            "ranked",
            get_settings().matchmaking_queue_lease_seconds,
        ))

    @staticmethod
    def _decode(value) -> str:
        return value.decode() if isinstance(value, bytes) else str(value)


async def assign_pair(
    redis: Redis,
    mode: str,
    pair: tuple[str, str],
    game_id: uuid.UUID,
    claim: str,
    ttl_seconds: int = 600,
) -> bool:
    first, second = pair
    result = await redis.eval(
        _ASSIGN_PAIR_SCRIPT,
        7,
        matchmaking_assignment_key(first),
        matchmaking_claim_key(first),
        matchmaking_owner_key(first),
        matchmaking_assignment_key(second),
        matchmaking_claim_key(second),
        matchmaking_owner_key(second),
        matchmaking_ranked_flux_key(),
        mode,
        str(game_id),
        ttl_seconds,
        claim,
        first,
        second,
    )
    return bool(result)


async def release_pair(
    redis: Redis,
    mode: str,
    pair: tuple[str, str],
    claim: str,
    blocked_user_ids: set[str] | None = None,
) -> bool:
    first, second = pair
    blocked_user_ids = blocked_user_ids or set()
    queue_key = matchmaking_ranked_queue_key() if mode == "ranked" else matchmaking_queue_key(mode)
    result = await redis.eval(
        _RELEASE_PAIR_SCRIPT,
        6,
        queue_key,
        matchmaking_ranked_flux_key(),
        matchmaking_claim_key(first),
        matchmaking_claim_key(second),
        matchmaking_assignment_key(first),
        matchmaking_assignment_key(second),
        mode,
        claim,
        int(time.time() * 1000),
        first,
        second,
        "matchmaking:{state}:claim:",
        "matchmaking:{state}:user-queue:",
        "1" if first in blocked_user_ids else "0",
        "1" if second in blocked_user_ids else "0",
        get_settings().matchmaking_queue_lease_seconds,
    )
    return bool(result)
