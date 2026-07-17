from core.identity import handle_identity
from core.time_context import handle_time_question
from core.policies import should_force_live_web

def route_message(message: str):
    hit = handle_identity(message)
    if hit:
        return {"mode": "direct", "payload": hit}

    hit = handle_time_question(message)
    if hit:
        return {"mode": "direct", "payload": hit}

    if should_force_live_web(message):
        return {"mode": "live_web"}

    return {"mode": "brain"}
