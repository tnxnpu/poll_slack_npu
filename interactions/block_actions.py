# interactions/block_actions.py

import httpx
import json
from bson.objectid import ObjectId
from fastapi import Response

from db import polls, drafts
from settings import SLACK_BOT_TOKEN
from view_loader import get_create_poll_modal
from .poll_helpers import update_all_poll_messages, EMOJI_LIST


async def _handle_vote(data: dict) -> Response:
    """Handles a user clicking a vote button."""
    action = data["actions"][0]
    user_id = data["user"]["id"]
    channel_id = data["channel"]["id"]
    message_ts = data["message"]["ts"]
    selected_choice_id = ObjectId(action["value"])

    poll = polls.find_one({"messages": {"$elemMatch": {"ts": message_ts, "channel": channel_id}}})
    if not poll:
        return Response(status_code=404, content="Poll not found from message context.")

    allow_multiple = poll.get("allow_multiple_votes", False)
    voter_query = {"_id": poll["_id"], "choices": {"$elemMatch": {"_id": selected_choice_id, "voters": user_id}}}
    already_voted = polls.find_one(voter_query)

    if not allow_multiple:
        polls.update_one({"_id": poll["_id"]}, {"$pull": {"choices.$[].voters": user_id}})
        if not already_voted:
            polls.update_one({"_id": poll["_id"], "choices._id": selected_choice_id},
                             {"$push": {"choices.$.voters": user_id}})
    else:
        if already_voted:
            polls.update_one({"_id": poll["_id"], "choices._id": selected_choice_id},
                             {"$pull": {"choices.$.voters": user_id}})
        else:
            polls.update_one({"_id": poll["_id"], "choices._id": selected_choice_id},
                             {"$addToSet": {"choices.$.voters": user_id}})

    async with httpx.AsyncClient() as client:
        await update_all_poll_messages(poll["_id"], client)
    return Response(status_code=200)


async def _handle_add_option_to_modal(data: dict) -> Response:
    """Handles adding a new option input field to the create/edit poll modal."""
    view = data["view"]
    blocks = view["blocks"]
    choice_count = sum(1 for b in blocks if b.get("block_id", "").startswith("choice_block_"))

    new_input_block = {
        "type": "input", "block_id": f"choice_block_{choice_count}", "optional": True,
        "label": {"type": "plain_text", "text": f"Option {choice_count + 1}"},
        "element": {"type": "plain_text_input", "action_id": f"choice_input_{choice_count}",
                    "placeholder": {"type": "plain_text", "text": "Enter option text"}}
    }

    insert_pos = next((i for i, b in enumerate(blocks) if b.get("block_id") == "add_option_section"), -1)
    if insert_pos != -1:
        blocks.insert(insert_pos, new_input_block)

    updated_view = {
        "type": "modal", "callback_id": view["callback_id"], "private_metadata": view["private_metadata"],
        "title": view["title"], "submit": view["submit"], "close": view["close"], "blocks": blocks
    }

    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
    async with httpx.AsyncClient() as client:
        await client.post("https://slack.com/api/views.update", headers=headers, json={
            "view_id": view["id"], "hash": view["hash"], "view": updated_view
        })
    return Response(status_code=200)


async def _handle_delete_poll_confirmed(data: dict) -> Response:
    """Handles deleting a poll from the settings modal AFTER confirmation."""
    private_metadata = json.loads(data["view"]["private_metadata"])
    poll_id = ObjectId(private_metadata["poll_id"])
    user_id = data["user"]["id"]
    poll = polls.find_one({"_id": poll_id})

    if not poll or poll.get("creator_id") != user_id:
        return Response(status_code=200)

    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
    async with httpx.AsyncClient() as client:
        for msg_info in poll.get("messages", []):
            await client.post("https://slack.com/api/chat.delete", headers=headers,
                              json={"channel": msg_info["channel"], "ts": msg_info["ts"]})

        polls.delete_one({"_id": poll_id})

        success_view = {
            "type": "modal", "title": {"type": "plain_text", "text": "Success"},
            "close": {"type": "plain_text", "text": "Close"},
            "blocks": [
                {"type": "section", "text": {"type": "plain_text", "text": "The poll was successfully deleted."}}]
        }
        await client.post("https://slack.com/api/views.update", headers=headers,
                          json={"view_id": data["view"]["id"], "hash": data["view"]["hash"], "view": success_view})
    return Response(status_code=200)


async def _handle_open_delete_confirmation_modal(data: dict) -> Response:
    """Replaces the settings modal with a dedicated delete confirmation modal."""
    private_metadata = data["view"]["private_metadata"]

    confirm_view = {
        "type": "modal",
        "callback_id": "delete_poll_confirmation",
        "private_metadata": private_metadata,
        "title": {"type": "plain_text", "text": "Delete Poll"},
        "submit": {"type": "plain_text", "text": "Delete", "emoji": True},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Are you sure you want to delete this poll? This action is irreversible."
                }
            }
        ]
    }

    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
    async with httpx.AsyncClient() as client:
        await client.post(
            "https://slack.com/api/views.update",
            headers=headers,
            json={"view_id": data["view"]["id"], "hash": data["view"]["hash"], "view": confirm_view}
        )
    return Response(status_code=200)


async def _handle_open_poll_settings(data: dict) -> Response:
    """Handles opening the settings modal from the overflow menu."""
    user_id = data["user"]["id"]
    action = data["actions"][0]
    poll_id_str = action["selected_option"]["value"].split("_")[1]
    poll_id = ObjectId(poll_id_str)
    poll = polls.find_one({"_id": poll_id})
    if not poll:
        return Response(status_code=200)

    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
    view = {}
    if poll.get("creator_id") != user_id:
        view = {
            "type": "modal", "title": {"type": "plain_text", "text": "Poll Information"},
            "close": {"type": "plain_text", "text": "Close"},
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": f"*Question:* {poll.get('question', 'N/A')}"}},
                {"type": "context",
                 "elements": [{"type": "mrkdwn", "text": f"Created by <@{poll.get('creator_id', 'N/A')}>"}]}
            ]
        }
    else:
        view = {
            "type": "modal", "callback_id": "poll_settings_modal",
            "private_metadata": json.dumps({"poll_id": str(poll_id)}),
            "title": {"type": "plain_text", "text": "Poll Settings"},
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": f"*Question:* {poll.get('question', 'N/A')}"}},
                {"type": "divider"},
                {"type": "section", "text": {"type": "mrkdwn", "text": "*Admin Controls*"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": "Edit the poll's question and options."},
                 "accessory": {"type": "button", "text": {"type": "plain_text", "text": "Edit Poll", "emoji": True},
                               "action_id": "edit_poll_content"}},
                {"type": "section",
                 "text": {"type": "mrkdwn", "text": "Permanently delete this poll from all channels."},
                 "accessory": {
                     "type": "button",
                     "text": {"type": "plain_text", "text": "Delete Poll", "emoji": True},
                     "style": "danger",
                     "action_id": "open_delete_confirmation_modal",
                 }}
            ]
        }

    async with httpx.AsyncClient() as client:
        await client.post("https://slack.com/api/views.open", headers=headers,
                          json={"trigger_id": data["trigger_id"], "view": view})
    return Response(status_code=200)


async def _handle_edit_poll_content(data: dict) -> Response:
    """Handles updating the settings modal to show the edit poll view."""
    user_id = data["user"]["id"]
    private_metadata = json.loads(data["view"]["private_metadata"])
    poll_id = ObjectId(private_metadata["poll_id"])
    poll = polls.find_one({"_id": poll_id})
    if not poll or poll.get("creator_id") != user_id:
        return Response(status_code=200)

    edit_blocks = [
        {"type": "input", "block_id": "question_block", "label": {"type": "plain_text", "text": "Poll Question"},
         "element": {"type": "plain_text_input", "action_id": "question_input",
                     "initial_value": poll.get("question", "")}}
    ]
    for i, choice in enumerate(poll.get("choices", [])):
        label_text = f"Option {i + 1}"
        # The 'optional' property below will add the (optional) text automatically.
        # No need to add it to the label_text manually.

        edit_blocks.append({
            "type": "input", "block_id": f"choice_block_{i}",
            "optional": i > 0,
            "label": {"type": "plain_text", "text": label_text},
            "element": {"type": "plain_text_input", "action_id": f"choice_input_{i}",
                        "initial_value": choice.get("text", "")}
        })

    edit_blocks.append({
        "type": "actions", "block_id": "add_option_section",
        "elements": [{"type": "button", "text": {"type": "plain_text", "text": "Add another option"},
                      "action_id": "add_option_to_modal"}]
    })

    initial_settings = []
    if poll.get("allow_others_to_add_options"):
        initial_settings.append(
            {"text": {"type": "plain_text", "text": "Allow others to add options", "emoji": True},
             "value": "allow_others_to_add"}
        )

    settings_block = {
        "type": "input", "block_id": "settings_block", "optional": True,
        "label": {"type": "plain_text", "text": "Settings"},
        "element": {
            "type": "checkboxes", "action_id": "settings_checkboxes",
            "options": [
                {"text": {"type": "plain_text", "text": "Allow others to add options", "emoji": True},
                 "value": "allow_others_to_add"}
            ]
        }
    }
    if initial_settings:
        settings_block["element"]["initial_options"] = initial_settings

    edit_blocks.append(settings_block)

    edit_modal_view = {
        "type": "modal", "callback_id": "submit_edit_poll_modal",
        "private_metadata": json.dumps({"poll_id": str(poll_id)}),
        "title": {"type": "plain_text", "text": "Edit Poll"}, "submit": {"type": "plain_text", "text": "Save"},
        "close": {"type": "plain_text", "text": "Cancel"}, "blocks": edit_blocks
    }

    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
    async with httpx.AsyncClient() as client:
        await client.post("https://slack.com/api/views.update", headers=headers,
                          json={"view_id": data["view"]["id"], "hash": data["view"]["hash"], "view": edit_modal_view})
    return Response(status_code=200)


async def _handle_view_poll_details(data: dict) -> Response:
    """Handles the 'Quick View' button to show poll results in a modal."""
    trigger_id = data["trigger_id"]
    action = data["actions"][0]
    poll_id = ObjectId(action["value"])
    poll = polls.find_one({"_id": poll_id})
    if not poll:
        return Response(status_code=404, content="Poll not found")

    total_votes = sum(len(c.get("voters", [])) for c in poll.get("choices", []))
    unique_voters = {v for c in poll.get("choices", []) for v in c.get("voters", [])}
    total_respondents = len(unique_voters)
    allow_multiple = poll.get("allow_multiple_votes", False)

    modal_blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{poll.get('question')}*"}}]
    if allow_multiple:
        modal_blocks.append(
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "💡 _Multiple votes are allowed_"}]})

    for i, choice in enumerate(poll.get("choices", [])):
        voters = choice.get("voters", [])
        vote_count = len(voters)
        percentage_base = total_votes if allow_multiple else total_respondents
        percentage = (vote_count / percentage_base * 100) if percentage_base > 0 else 0
        mention_text = " ".join(f"<@{uid}>" for uid in voters) if voters else "_No votes yet_"
        emoji = EMOJI_LIST[i] if i < len(EMOJI_LIST) else "🔘"
        modal_blocks.extend([
            {"type": "section", "text": {"type": "mrkdwn",
                                         "text": f"{emoji} *{choice.get('text')}* (`{vote_count}` votes | {percentage:.0f}%)"}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": mention_text}]}
        ])

    modal_blocks.append({"type": "divider"})
    modal_blocks.append(
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"Created by <@{poll.get('creator_id')}>"}]})

    view = {"type": "modal", "title": {"type": "plain_text", "text": "Poll Details"},
            "close": {"type": "plain_text", "text": "Close"}, "blocks": modal_blocks}
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
    async with httpx.AsyncClient() as client:
        await client.post("https://slack.com/api/views.open", headers=headers,
                          json={"trigger_id": trigger_id, "view": view})
    return Response(status_code=200)


async def _handle_open_create_poll_modal(data: dict) -> Response:
    """Handles the 'Create a new poll' button from the App Home."""
    trigger_id = data.get("trigger_id")
    user_id = data["user"]["id"]
    channel_id = data.get("channel", {}).get("id")

    draft_state = None
    if user_id:
        draft_doc = drafts.find_one({"user_id": user_id})
        if draft_doc:
            draft_state = draft_doc.get("state")

    modal = get_create_poll_modal(trigger_id, channel_id, draft_state)
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
    async with httpx.AsyncClient() as client:
        await client.post("https://slack.com/api/views.open", headers=headers, json=modal)
    return Response(status_code=200)


async def _handle_open_add_option_modal(data: dict) -> Response:
    """Handles the 'Add option' button on a poll message."""
    action = data["actions"][0]
    poll_id_str = action["value"]
    trigger_id = data["trigger_id"]
    view = {
        "type": "modal", "callback_id": "submit_add_option_modal",
        "private_metadata": json.dumps({"poll_id": poll_id_str}),
        "title": {"type": "plain_text", "text": "Add an Option"},
        "submit": {"type": "plain_text", "text": "Add"}, "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {"type": "input", "block_id": "new_option_block",
             "label": {"type": "plain_text", "text": "New option text"},
             "element": {"type": "plain_text_input", "action_id": "new_option_input",
                         "placeholder": {"type": "plain_text", "text": "What's the new option?"}}},
            {"type": "input", "optional": True, "block_id": "vote_for_option_block",
             "label": {"type": "plain_text", "text": " "},
             "element": {"type": "checkboxes", "action_id": "vote_for_option_checkbox",
                         "options": [{"text": {"type": "plain_text", "text": "Vote for this option right away"},
                                      "value": "vote_now"}]}}
        ]
    }
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
    async with httpx.AsyncClient() as client:
        await client.post("https://slack.com/api/views.open", headers=headers,
                          json={"trigger_id": trigger_id, "view": view})
    return Response(status_code=200)


# A dictionary to map action_ids to their handler functions
BLOCK_ACTION_HANDLERS = {
    "vote_for_choice": _handle_vote,
    "add_option_to_modal": _handle_add_option_to_modal,
    "delete_poll_from_settings": _handle_delete_poll_confirmed,
    "open_delete_confirmation_modal": _handle_open_delete_confirmation_modal,
    "open_poll_settings": _handle_open_poll_settings,
    "edit_poll_content": _handle_edit_poll_content,
    "view_poll_details": _handle_view_poll_details,
    "open_create_poll_modal": _handle_open_create_poll_modal,
    "open_add_option_modal": _handle_open_add_option_modal,
}


async def handle_block_actions(data: dict) -> Response:
    """Routes block actions to the correct handler based on action_id."""
    action_id = data["actions"][0]["action_id"]
    if handler := BLOCK_ACTION_HANDLERS.get(action_id):
        return await handler(data)
    return Response(status_code=404, content=f"No handler for block action '{action_id}'")
