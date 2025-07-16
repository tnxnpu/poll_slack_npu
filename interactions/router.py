# interactions/router.py

import json
from fastapi import APIRouter, Request, Response

# Use relative imports for handlers within the same package
from .view_submission import handle_view_submission
from .block_actions import handle_block_actions

interactions_router = APIRouter()


@interactions_router.post("/slack/interactions", tags=["Slack Interactions"])
async def handle_interactions(request: Request):
    """
    Main interactions endpoint.
    Parses the payload from Slack and routes it to the appropriate handler
    based on the interaction type.
    """
    try:
        payload = await request.form()
        data = json.loads(payload.get("payload"))
        interaction_type = data.get("type")

        # Route to the handler based on the interaction type
        if interaction_type == "view_submission":
            return await handle_view_submission(data)

        elif interaction_type == "block_actions":
            return await handle_block_actions(data)

        # Fallback for unhandled interaction types
        return Response(status_code=400, content=f"Unsupported interaction type: {interaction_type}")

    except json.JSONDecodeError:
        return Response(status_code=400, content="Invalid JSON payload.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return Response(status_code=500, content="Internal Server Error")
