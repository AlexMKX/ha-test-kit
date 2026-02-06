import asyncio


def _ensure_current_event_loop() -> None:
    try:
        asyncio.get_event_loop()
        return
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)


_ensure_current_event_loop()
