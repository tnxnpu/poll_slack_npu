# view_loader.py
import json
from typing import Optional

def get_create_poll_modal(trigger_id: str, channel_id: Optional[str] = None) -> dict:
    """
    Loads the 'create poll' modal view from a JSON file and populates it
    with dynamic data like trigger_id and initial_channel.
    """
    with open("views/create_poll_modal.json") as f:
        view_payload = json.load(f)

    # Find the channel selection block by its ID to set the initial conversation
    for block in view_payload.get("blocks", []):
        if block.get("block_id") == "channel_block":
            block["element"]["initial_conversations"] = [channel_id] if channel_id else []
            break

    # Construct the final modal payload for the Slack API
    modal = {
        "trigger_id": trigger_id,
        "view": view_payload
    }

    return modal