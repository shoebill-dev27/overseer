"""WebSocket endpoint for real-time session state updates."""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..sessions import COOKIE_NAME, get_session_user
from ..ws_manager import manager

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/sessions")
async def ws_sessions(websocket: WebSocket) -> None:
    # Authenticate before accepting the connection
    token = websocket.cookies.get(COOKIE_NAME)
    if not token:
        await websocket.close(code=4001)
        return

    # Use a one-off DB connection for auth check
    import aiosqlite
    from ..config import settings

    async with aiosqlite.connect(settings.database_path) as db:
        db.row_factory = aiosqlite.Row
        user = await get_session_user(db, token)

    if user is None:
        await websocket.close(code=4001)
        return

    await manager.connect(websocket)
    try:
        # Keep the connection alive; client sends pings
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
