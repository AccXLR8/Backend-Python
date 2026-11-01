from app.matchmaking.modes import MatchmakingMode


def matchmaking_queue_key(mode: str = MatchmakingMode.STANDARD.value) -> str:
    return f"matchmaking:{{state}}:queue:{mode}"


def game_lock_key(game_id: str) -> str:
    return f"game:{game_id}:lock"


def game_state_key(game_id: str) -> str:
    return f"game:{game_id}:state"


def connection_set_key(game_id: str) -> str:
    return f"game:{game_id}:connections"


def matchmaking_assignment_key(user_id: str) -> str:
    return f"matchmaking:{{state}}:assignment:{user_id}"


def matchmaking_claim_key(user_id: str) -> str:
    return f"matchmaking:{{state}}:claim:{user_id}"


def matchmaking_owner_key(user_id: str) -> str:
    return f"matchmaking:{{state}}:user-queue:{user_id}"


def matchmaking_ranked_queue_key() -> str:
    return "matchmaking:{state}:queue:ranked:8x8"


def matchmaking_ranked_flux_key() -> str:
    return "matchmaking:{state}:queue:ranked:8x8:flux"


def legacy_matchmaking_assignment_key(user_id: str) -> str:
    return f"matchmaking:assignment:{user_id}"


def _websocket_presence_tag(user_id: str) -> str:
    return f"presence:{user_id}"


def websocket_user_connections_key(user_id: str) -> str:
    return f"websocket:{{{_websocket_presence_tag(user_id)}}}:user:{user_id}:connections"


def websocket_connection_key(connection_id: str, user_id: str) -> str:
    return f"websocket:{{{_websocket_presence_tag(user_id)}}}:connection:{connection_id}"


def websocket_connection_prefix(user_id: str) -> str:
    return f"websocket:{{{_websocket_presence_tag(user_id)}}}:connection:"


def websocket_ticket_key(ticket_hash: str) -> str:
    return f"websocket:{{ticket:{ticket_hash}}}:ticket"


def rate_limit_key(scope: str, user_id: str) -> str:
    return f"rate-limit:{{user:{user_id}}}:{scope}"
