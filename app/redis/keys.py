from app.matchmaking.modes import MatchmakingMode


def matchmaking_queue_key(mode: str = MatchmakingMode.STANDARD.value) -> str:
    return f"matchmaking:queue:{mode}"


def game_lock_key(game_id: str) -> str:
    return f"game:{game_id}:lock"


def game_state_key(game_id: str) -> str:
    return f"game:{game_id}:state"


def connection_set_key(game_id: str) -> str:
    return f"game:{game_id}:connections"


def matchmaking_assignment_key(user_id: str) -> str:
    return f"matchmaking:assignment:{user_id}"
