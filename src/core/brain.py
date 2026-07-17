async def hawk_brain(message: str, memory_context: str, *, build_text_response):
    agent_type, result, used_web = await build_text_response(
        message,
        memory_context,
        use_web=False
    )
    return {
        "ok": True,
        "response": result,
        "used_web": used_web,
        "agent": agent_type,
        "intent": "brain",
    }
