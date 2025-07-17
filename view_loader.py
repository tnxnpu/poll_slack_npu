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

    view_payload = copy.deepcopy(view_template)

    if draft_state:
        # --- Start of Refactored Draft Loading Logic ---

        # 1. Create a fresh list of blocks, removing the default choice blocks from the template.
        new_blocks = [
            b for b in view_payload["blocks"]
            if not b.get("block_id", "").startswith("choice_block_")
        ]

        # 2. Find the position where the new choices should be inserted (right before the "Add another option" button).
        insert_pos = next((i for i, b in enumerate(new_blocks) if b.get("block_id") == "add_option_section"), -1)
        if insert_pos == -1: insert_pos = 2  # Fallback position

        # 3. Create new choice blocks from the draft data and insert them.
        draft_choices = sorted(
            [(k, v) for k, v in draft_state.items() if k.startswith("choice_block_")],
            key=lambda item: int(item[0].split('_')[-1])
        )

        # Insert in reverse to maintain order
        for i, (block_id, block_data) in reversed(list(enumerate(draft_choices))):
            action_id = next(iter(block_data))
            new_blocks.insert(insert_pos, {
                "type": "input",
                "block_id": block_id,
                "optional": i > 0,
                "label": {"type": "plain_text", "text": f"Option {i + 1}"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": action_id,
                    "placeholder": {"type": "plain_text", "text": "Write something"}
                }
            })

        # 4. Add the context message at the top.
        new_blocks.insert(0, {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "📝 A previously saved draft has been loaded for you."}]
        })

        view_payload["blocks"] = new_blocks

        # 5. Populate all the blocks in the newly constructed view with the saved values.
        for block_id, block_data in draft_state.items():
            action_id = next(iter(block_data))
            value_dict = block_data[action_id]

            for block in view_payload.get("blocks", []):
                if block.get("block_id") == block_id:
                    element = block.get("element", {})
                    element_type = element.get("type")

                    if element_type == "plain_text_input":
                        initial_val = value_dict.get("value")
                        if initial_val is not None:
                            element["initial_value"] = initial_val
                    elif element_type == "multi_conversations_select":
                        element["initial_conversations"] = value_dict.get("selected_conversations", [])
                    elif element_type == "checkboxes":
                        element["initial_options"] = value_dict.get("selected_options", [])

                    break
        # --- End of Refactored Logic ---

    else:
        # This part runs if no draft is found.
        for block in view_payload.get("blocks", []):
            if block.get("block_id") == "channel_block":
                block["element"]["initial_conversations"] = [channel_id] if channel_id else []
                break

    modal = {
        "trigger_id": trigger_id,
        "view": view_payload
    }

    return modal
