# interactions/view_submission.py

import asyncio
import json
import httpx
from bson.objectid import ObjectId
from fastapi.responses import JSONResponse, Response

from db import polls, drafts
from settings import SLACK_BOT_TOKEN
from .poll_helpers import update_all_poll_messages


async def send_poll_to_channels(question, choices_data, channels, poll_id):
    """Sends the initial poll message to multiple Slack channels."""
    poll_doc = polls.find_one({"_id": poll_id})
    if not poll_doc: return

    # We import build_poll_blocks here to avoid circular dependency issues
    from .poll_helpers import build_poll_blocks
    blocks = build_poll_blocks(poll_doc)

    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    json_headers = {**headers, "Content-Type": "application/json; charset=utf-8"}

    async with httpx.AsyncClient() as client:
        for channel in channels:
            try:
                r = await client.post("https://slack.com/api/chat.postMessage", headers=json_headers,
                                      json={"channel": channel, "blocks": blocks, "text": question})
                r.raise_for_status()
                response_json = r.json()

                if response_json.get("ok"):
                    ts = response_json["ts"]
                    channel_id = response_json["channel"]

                    perm_r = await client.get("https://slack.com/api/chat.getPermalink",
                                              headers=headers, params={"channel": channel_id, "message_ts": ts})
                    permalink = perm_r.json().get("permalink", "") if perm_r.status_code == 200 and perm_r.json().get(
                        "ok") else ""

                    polls.update_one(
                        {"_id": poll_id},
                        {"$push": {"messages": {"channel": channel_id, "ts": ts, "permalink": permalink}}}
                    )
                else:
                    print(f"Error sending poll to channel {channel}: {response_json.get('error')}")

            except httpx.HTTPStatusError as e:
                print(f"HTTP error sending poll to channel {channel}: {e.response.status_code} {e.response.text}")
            except Exception as e:
                print(f"An unexpected error occurred sending poll to channel {channel}: {e}")


def _build_invite_required_view(not_joined_channels: list, bot_name: str = "YourBotName") -> dict:
    """Builds the modal view telling the user to invite the bot."""
    channel_mentions = ", ".join(f"<#{c}>" for c in not_joined_channels)
    return {
        "type": "modal",
        "title": {"type": "plain_text", "text": "Invitation Required"},
        "close": {"type": "plain_text", "text": "Close"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"⚠️ *The poll bot needs to be invited into {channel_mentions}*"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"To post your poll, the bot must be a member of the selected channel(s) first.\n\n*Here's how:*\n1. Close this window.\n2. Go to the channel(s).\n3. Type `/invite @{bot_name}` and send.\n4. Re-open the poll creator. Your draft has been saved!"
                }
            }
        ]
    }


def _extract_choices(state: dict) -> list[str]:
    """Extracts all choice text values from the modal state."""
    choices = []
    # Sort blocks to maintain choice order, as dictionary iteration order is not guaranteed
    sorted_blocks = sorted(state.items(), key=lambda item: item[0])
    for block_id, block_data in sorted_blocks:
        if block_id.startswith("choice_block_"):
            # The action_id is the key inside the block_data dictionary
            action_id = next(iter(block_data))
            if choice_text := block_data[action_id].get("value"):
                choices.append(choice_text.strip())
    return choices


async def _handle_submit_poll(data: dict) -> Response:
    """Handles the submission of the 'Create Poll' modal."""
    view_state = data["view"]["state"]["values"]
    user_id = data["user"]["id"]

    question = view_state["question_block"]["question_input"]["value"]
    choices_text = _extract_choices(view_state)
    channels = view_state["channel_block"]["channels_input"]["selected_conversations"]

    selected_options = view_state.get("settings_block", {}).get("settings_checkboxes", {}).get("selected_options", [])
    selected_values = {opt['value'] for opt in selected_options}

    # Basic validation before checking channels
    if not all([question, choices_text, channels]):
        errors = {}
        if not question: errors["question_block"] = "A question is required."
        if not choices_text: errors["choice_block_0"] = "At least one option is required."
        if not channels: errors["channel_block"] = "At least one channel must be selected."
        return JSONResponse(content={"response_action": "errors", "errors": errors})

    # --- Channel Membership Validation ---
    not_joined_channels = []
    bot_info = {}
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    async with httpx.AsyncClient() as client:
        # First, get the bot's own user ID to get its name later
        try:
            auth_test_res = await client.post("https://slack.com/api/auth.test", headers=headers)
            auth_test_res.raise_for_status()
            bot_info = auth_test_res.json()
        except Exception as e:
            print(f"Error getting bot info: {e}")

        for channel_id in channels:
            try:
                response = await client.get(
                    "https://slack.com/api/conversations.info",
                    headers=headers,
                    params={"channel": channel_id}
                )
                response.raise_for_status()
                channel_info = response.json()
                # The bot is not a member of the channel
                if channel_info.get("ok") and not channel_info.get("channel", {}).get("is_member"):
                    not_joined_channels.append(channel_id)
                # The API call itself failed, e.g. channel not found for the whole workspace
                elif not channel_info.get("ok"):
                    not_joined_channels.append(channel_id)

            except httpx.HTTPStatusError as e:
                # This often means the bot can't even see the channel exists (e.g. private channel)
                print(f"HTTP error checking channel info for {channel_id}: {e.response.text}")
                not_joined_channels.append(channel_id)
            except Exception as e:
                print(f"An unexpected error occurred checking channel {channel_id}: {e}")
                not_joined_channels.append(channel_id)

    if not_joined_channels:
        # Save the user's input as a draft
        drafts.update_one(
            {"user_id": user_id},
            {"$set": {"state": view_state}},
            upsert=True
        )
        # Build and return the instructional view
        bot_name = bot_info.get("user", "YourBotName")  # Fallback name
        error_view = _build_invite_required_view(list(set(not_joined_channels)), bot_name)
        return JSONResponse(content={"response_action": "update", "view": error_view})

    # --- End of Validation ---

    if 'tag_channel' in selected_values:
        question = " <!channel> " + question

    choices = [{"_id": ObjectId(), "text": text, "voters": []} for text in choices_text if text]

    poll_doc = {
        "question": question, "choices": choices, "channels": channels,
        "creator_id": user_id, "messages": [],
        "allow_multiple_votes": 'allow_multiple' in selected_values,
        "allow_others_to_add_options": 'allow_others_to_add' in selected_values
    }
    result = polls.insert_one(poll_doc)

    # Clear the draft since the poll was created successfully
    drafts.delete_one({"user_id": user_id})

    asyncio.create_task(send_poll_to_channels(question, choices, channels, result.inserted_id))
    return Response(status_code=200)


async def _handle_edit_poll(data: dict) -> Response:
    """Handles the submission of the 'Edit Poll' modal."""
    view_state = data["view"]["state"]["values"]
    user_id = data["user"]["id"]
    private_metadata = json.loads(data["view"]["private_metadata"])
    poll_id = ObjectId(private_metadata["poll_id"])
    poll = polls.find_one({"_id": poll_id})

    if not poll or poll.get("creator_id") != user_id:
        return JSONResponse(content={"response_action": "errors",
                                     "errors": {"question_block": "You are not authorized to edit this poll."}})

    new_question = view_state["question_block"]["question_input"]["value"]
    new_choices_text = _extract_choices(view_state)

    selected_options = view_state.get("settings_block", {}).get("settings_checkboxes", {}).get("selected_options", [])
    selected_values = {opt['value'] for opt in selected_options}
    allow_others_to_add_options = 'allow_others_to_add' in selected_values

    old_choices_data = poll.get("choices", [])
    new_choices_data = []
    for i, new_text in enumerate(new_choices_text):
        if i < len(old_choices_data):
            # Preserve existing choice ID and voters
            new_choices_data.append(
                {"_id": old_choices_data[i]["_id"], "text": new_text, "voters": old_choices_data[i]["voters"]})
        else:
            # This is a brand new choice
            new_choices_data.append({"_id": ObjectId(), "text": new_text, "voters": []})

    polls.update_one(
        {"_id": poll_id},
        {"$set": {
            "question": new_question,
            "choices": new_choices_data,
            "allow_others_to_add_options": allow_others_to_add_options
        }}
    )
    async with httpx.AsyncClient() as client:
        await update_all_poll_messages(poll_id, client)
    return Response(status_code=200)


async def _handle_add_option(data: dict) -> Response:
    """Handles the submission of the 'Add Option' modal."""
    view_state = data["view"]["state"]["values"]
    user_id = data["user"]["id"]
    private_metadata = json.loads(data["view"]["private_metadata"])
    poll_id = ObjectId(private_metadata["poll_id"])
    new_option_text = view_state["new_option_block"]["new_option_input"]["value"]

    if not new_option_text:
        return JSONResponse(
            content={"response_action": "errors", "errors": {"new_option_block": "Option text cannot be empty."}})

    vote_for_it = bool(
        view_state.get("vote_for_option_block", {}).get("vote_for_option_checkbox", {}).get("selected_options"))
    voters = [user_id] if vote_for_it else []
    new_choice = {"_id": ObjectId(), "text": new_option_text.strip(), "voters": voters}

    poll = polls.find_one({"_id": poll_id})
    if poll:
        # If user votes for the new option, and it's not a multi-vote poll, remove their other votes
        if vote_for_it and not poll.get("allow_multiple_votes", False):
            polls.update_one({"_id": poll_id}, {"$pull": {"choices.$[].voters": user_id}})

        polls.update_one({"_id": poll_id}, {"$push": {"choices": new_choice}})

        async with httpx.AsyncClient() as client:
            await update_all_poll_messages(poll_id, client)
    return Response(status_code=200)


async def _handle_delete_poll_confirmed(data: dict) -> Response:
    """Handles deleting a poll after the user confirms in the dedicated modal."""
    private_metadata = json.loads(data["view"]["private_metadata"])
    poll_id = ObjectId(private_metadata["poll_id"])
    user_id = data["user"]["id"]
    poll = polls.find_one({"_id": poll_id})

    if not poll or poll.get("creator_id") != user_id:
        return Response(status_code=403)  # Forbidden

    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    async with httpx.AsyncClient() as client:
        for msg_info in poll.get("messages", []):
            await client.post("https://slack.com/api/chat.delete", headers=headers,
                              json={"channel": msg_info["channel"], "ts": msg_info["ts"]})

    polls.delete_one({"_id": poll_id})

    # Return an empty 200 OK response to close the modal.
    return Response(status_code=200)


# A dictionary to map callback_ids to their handler functions
VIEW_SUBMISSION_HANDLERS = {
    "submit_poll_modal": _handle_submit_poll,
    "submit_edit_poll_modal": _handle_edit_poll,
    "submit_add_option_modal": _handle_add_option,
    "delete_poll_confirmation": _handle_delete_poll_confirmed,
}


async def handle_view_submission(data: dict) -> Response:
    """Routes view submissions to the correct handler based on callback_id."""
    callback_id = data["view"]["callback_id"]
    if handler := VIEW_SUBMISSION_HANDLERS.get(callback_id):
        return await handler(data)
    return Response(status_code=404, content=f"No handler for view submission '{callback_id}'")

