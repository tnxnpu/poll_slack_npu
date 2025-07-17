# view_loader.py
import json
import copy
from typing import Optional, Dict, Any


def get_create_poll_modal(trigger_id: str, channel_id: Optional[str] = None,
                          draft_state: Optional[Dict[str, Any]] = None) -> dict:
    """
    Loads the 'create poll' modal view from a JSON file and populates it
    with dynamic data like trigger_id, initial_channel, and saved draft state.
    """
    with open("views/create_poll_modal.json") as f:
        view_template = json.load(f)

    # Use a deep copy to prevent modifying the original template in memory
    view_payload = copy.deepcopy(view_template)

    # --- Draft Population Logic ---
    if draft_state:
        # First, dynamically build the blocks to match the draft's structure
        new_blocks = []

        # Add the "draft loaded" notice
        new_blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "📝 A previously saved draft has been loaded for you."}]
        })

        # Find and add the question block from the template
        question_template = next((b for b in view_template["blocks"] if b.get("block_id") == "question_block"), None)
        if question_template:
            new_blocks.append(question_template)

        # Find all choices in the draft and create input blocks for them, preserving their original IDs
        draft_choices = sorted(
            [(k, v) for k, v in draft_state.items() if k.startswith("choice_block_")],
            key=lambda item: int(item[0].split('_')[-1])
        )
        for i, (block_id, block_data) in enumerate(draft_choices):
            # Extract the original action_id from the draft data
            action_id = next(iter(block_data))
            new_blocks.append({
                "type": "input",
                "block_id": block_id,
                # --- CORRECTED LOGIC ---
                # The first option (i=0) is required, the rest are optional.
                "optional": i > 0,
                "label": {"type": "plain_text", "text": f"Option {i + 1}"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": action_id,
                    "placeholder": {"type": "plain_text", "text": "Write something"}
                }
            })

        # Find and add the remaining blocks (add option, settings, channels) from the template
        for block in view_template["blocks"]:
            template_block_id = block.get("block_id", "")
            if not template_block_id.startswith("question_block") and not template_block_id.startswith("choice_block_"):
                new_blocks.append(block)

        # Replace the template's blocks with our dynamically generated ones
        view_payload["blocks"] = new_blocks

        # Second, populate the newly structured view with values from the draft
        for block_id, block_data in draft_state.items():
            action_id = next(iter(block_data))
            value_dict = block_data[action_id]

            # Find the corresponding block in our new structure and populate it
            for block in view_payload.get("blocks", []):
                if block.get("block_id") == block_id:
                    element = block.get("element", {})
                    element_type = element.get("type")

                    if element_type == "plain_text_input":
                        element["initial_value"] = value_dict.get("value")
                    elif element_type == "multi_conversations_select":
                        element["initial_conversations"] = value_dict.get("selected_conversations")
                    elif element_type == "checkboxes":
                        element["initial_options"] = value_dict.get("selected_options")

                    break

    # If no draft, set the initial channel from the command context
    else:
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
