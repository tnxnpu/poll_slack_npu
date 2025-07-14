# interactions.py
import asyncio
import json

import httpx
from bson.objectid import ObjectId
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from db import polls
from settings import SLACK_BOT_TOKEN

interactions_router = APIRouter()


async def update_slack_poll_message(poll_id: ObjectId, client: httpx.AsyncClient):
    """
    Fetches a poll by its ID, rebuilds the Slack message with the latest data,
    and updates the message in Slack using chat.update.
    This is a helper function to be used after votes or edits.
    """
    poll = polls.find_one({"_id": poll_id})
    if not poll:
        print(f"Cannot update message for poll {poll_id}: Poll not found.")
        return

    message_ts = poll.get("message_ts")
    channel = poll.get("channel")
    if not message_ts or not channel:
        print(f"Cannot update message for poll {poll_id}: Missing message_ts or channel.")
        return

    # --- Calculate Vote Counts and Percentages ---
    all_votes_flat = {}
    unique_voters = set()
    total_individual_votes_cast = 0
    allow_multiple = poll.get("allow_multiple_votes", False)

    for user_id, user_vote_data in poll.get("votes", {}).items():
        if user_vote_data:
            unique_voters.add(user_id)
            if isinstance(user_vote_data, list):
                for choice_item in user_vote_data:
                    all_votes_flat.setdefault(choice_item, []).append(user_id)
                    total_individual_votes_cast += 1
            else:  # Single vote is a string
                all_votes_flat.setdefault(user_vote_data, []).append(user_id)
                total_individual_votes_cast += 1

    total_respondents = len(unique_voters)

    # --- Build Slack Message Blocks ---
    question = poll["question"]
    choices = poll["choices"]
    creator_id = poll.get("creator_id", "unknown")
    emoji_list = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{question}*"},
            "accessory": {
                "type": "overflow",
                "options": [
                    {
                        "text": {"type": "plain_text", "text": "Settings", "emoji": True},
                        "value": f"settings_{poll['_id']}"
                    }
                ],
                "action_id": "open_poll_settings"
            }
        }
    ]

    if allow_multiple:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "💡 _You may vote for multiple options_"}]
        })

    for i, choice in enumerate(choices):
        current_emoji = emoji_list[i] if i < len(emoji_list) else "🔘"
        user_ids_for_this_choice = all_votes_flat.get(choice, [])
        vote_count = len(user_ids_for_this_choice)

        percentage_base = total_individual_votes_cast if allow_multiple else total_respondents
        percentage = (vote_count / percentage_base * 100) if percentage_base > 0 else 0

        mention_text = " ".join(f"<@{uid}>" for uid in user_ids_for_this_choice) if user_ids_for_this_choice else ""

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"{current_emoji} *{choice}* | *{percentage:.0f}%* `{vote_count}`\n{mention_text}"},
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": current_emoji},
                "value": choice,
                "action_id": f"vote_option_{i}"
            }
        })

    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": f"*Total votes:* {total_individual_votes_cast}"},
            {"type": "mrkdwn", "text": f"Created by <@{creator_id}>"}
        ]
    })

    # --- Send Update to Slack ---
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
    update_response = await client.post("https://slack.com/api/chat.update", headers=headers, json={
        "channel": channel,
        "ts": message_ts,
        "blocks": blocks,
        "text": question  # Fallback text for notifications
    })
    print(f"Helper: Slack chat.update response status: {update_response.status_code}, Text: {update_response.text}")


async def send_poll_to_slack(question, choices, channel, poll_id):
    """Sends the initial poll message to Slack."""
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
    poll_doc = polls.find_one({"_id": poll_id})
    allow_multiple_votes = poll_doc.get("allow_multiple_votes", False) if poll_doc else False

    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"* {question}*"},
            "accessory": {
                "type": "overflow",
                "options": [{"text": {"type": "plain_text", "text": "Settings", "emoji": True},
                             "value": f"settings_{poll_id}"}],
                "action_id": "open_poll_settings"
            }
        }
    ]

    if allow_multiple_votes:
        blocks.append(
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "💡 _You may vote for multiple options_"}]})

    emoji_list = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    for i, choice in enumerate(choices):
        current_emoji = emoji_list[i] if i < len(emoji_list) else "🔘"
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{current_emoji} *{choice}*"},
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": current_emoji},
                "value": choice,
                "action_id": f"vote_option_{i}"
            }
        })

    async with httpx.AsyncClient() as client:
        r = await client.post("https://slack.com/api/chat.postMessage", headers=headers,
                              json={"channel": channel, "blocks": blocks})
        if r.status_code == 200 and r.json().get("ok"):
            ts = r.json()["ts"]
            polls.update_one({"_id": poll_id}, {"$set": {"message_ts": ts}})
            print(f"📤 Poll sent, message_ts {ts} stored.")
        else:
            print(f"Error sending poll to slack: {r.status_code} {r.text}")


@interactions_router.post("/slack/interactions")
async def handle_interactions(request: Request):
    """Handles all incoming Slack interactions."""
    payload = await request.form()
    data = json.loads(payload.get("payload"))
    interaction_type = data["type"]
    user_id = data["user"]["id"]

    print(f"\n--- Incoming Interaction: {interaction_type} from {user_id} ---")

    # === Modal Submission Handler ===
    if interaction_type == "view_submission":
        callback_id = data["view"]["callback_id"]
        view_state = data["view"]["state"]["values"]

        # --- Handle new poll creation ---
        if callback_id == "submit_poll_modal":
            question = view_state["question_block"]["question_input"]["value"]
            choices_raw = view_state["choices_block"]["choices_input"]["value"]
            channel = data["view"]["private_metadata"]
            allow_multiple_votes = "multiple_votes_block" in view_state and \
                                   "allow_multiple_votes_checkbox" in view_state["multiple_votes_block"] and \
                                   bool(view_state["multiple_votes_block"]["allow_multiple_votes_checkbox"].get(
                                       "selected_options"))
            choices = [c.strip() for c in choices_raw.strip().split("\n") if c.strip()]

            poll_doc = {
                "question": question, "choices": choices, "channel": channel,
                "creator_id": user_id, "message_ts": None, "votes": {},
                "allow_multiple_votes": allow_multiple_votes
            }
            result = polls.insert_one(poll_doc)
            print(f"Poll inserted to DB: {result.inserted_id}")
            asyncio.create_task(send_poll_to_slack(question, choices, channel, result.inserted_id))
            return Response(status_code=200)

        # --- Handle poll edit submission ---
        elif callback_id == "submit_edit_poll_modal":
            private_metadata = json.loads(data["view"]["private_metadata"])
            poll_id = ObjectId(private_metadata["poll_id"])
            poll = polls.find_one({"_id": poll_id})

            if not poll or poll.get("creator_id") != user_id:
                return JSONResponse(content={"response_action": "errors", "errors": {
                    "edit_question_block": "You are not authorized to edit this poll."}})

            new_question = view_state["edit_question_block"]["edit_question_input"]["value"]
            new_choices_raw = view_state["edit_choices_block"]["edit_choices_input"]["value"]
            new_choices = [c.strip() for c in new_choices_raw.strip().split("\n") if c.strip()]

            # Update poll in DB and reset votes
            polls.update_one({"_id": poll_id},
                             {"$set": {"question": new_question, "choices": new_choices, "votes": {}}})
            print(f"Poll {poll_id} updated in DB. Votes have been reset.")

            async with httpx.AsyncClient() as client:
                await update_slack_poll_message(poll_id, client)
            return Response(status_code=200)  # Acknowledge to close modal

    # === Button Clicks & Overflow Menu Handler ===
    elif interaction_type == "block_actions":
        action = data["actions"][0]
        action_id = action["action_id"]

        is_from_modal = "view" in data
        poll_id_from_modal_metadata = None
        if is_from_modal:
            private_metadata = json.loads(data["view"]["private_metadata"])
            poll_id_from_modal_metadata = ObjectId(private_metadata["poll_id"])

        # --- Handle voting ---
        if action_id.startswith("vote_option_"):
            message_ts = data["message"]["ts"]
            channel_id = data["channel"]["id"]
            selected_choice = action["value"]
            poll = polls.find_one({"message_ts": message_ts, "channel": channel_id})

            if poll:
                allow_multiple = poll.get("allow_multiple_votes", False)
                current_user_votes = poll.get("votes", {}).get(user_id)

                if allow_multiple:
                    # Toggle vote in a list
                    if selected_choice in (current_user_votes or []):
                        polls.update_one({"_id": poll["_id"]}, {"$pull": {f"votes.{user_id}": selected_choice}})
                    else:
                        polls.update_one({"_id": poll["_id"]}, {"$addToSet": {f"votes.{user_id}": selected_choice}})
                else:
                    # Toggle vote for a single string value
                    if current_user_votes == selected_choice:
                        polls.update_one({"_id": poll["_id"]}, {"$unset": {f"votes.{user_id}": ""}})
                    else:
                        polls.update_one({"_id": poll["_id"]}, {"$set": {f"votes.{user_id}": selected_choice}})

                async with httpx.AsyncClient() as client:
                    await update_slack_poll_message(poll["_id"], client)

        # --- Handle opening settings modal ---
        elif action_id == "open_poll_settings":
            poll_id_str = action["selected_option"]["value"].split("_")[1]
            poll_id = ObjectId(poll_id_str)
            poll = polls.find_one({"_id": poll_id})
            headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}

            if not poll: return Response(status_code=200)

            # If user is not the creator, show a simple info modal
            if poll.get("creator_id") != user_id:
                view = {
                    "type": "modal", "title": {"type": "plain_text", "text": "Poll Information"},
                    "close": {"type": "plain_text", "text": "Close"},
                    "blocks": [
                        {"type": "section",
                         "text": {"type": "mrkdwn", "text": f"*Question:* {poll.get('question', 'N/A')}"}},
                        {"type": "context",
                         "elements": [{"type": "mrkdwn", "text": f"Created by <@{poll.get('creator_id', 'N/A')}>"}]}
                    ]
                }
            # If user is the creator, show the full settings modal
            else:
                view = {
                    "type": "modal", "callback_id": "poll_settings_modal",
                    "private_metadata": json.dumps({"poll_id": str(poll_id)}),
                    "title": {"type": "plain_text", "text": "Poll Settings"},
                    "blocks": [
                        {"type": "section", "text": {"type": "mrkdwn", "text": "Edit the content of this poll."},
                         "accessory": {"type": "button",
                                       "text": {"type": "plain_text", "text": "Edit Poll", "emoji": True},
                                       "action_id": "edit_poll_content"}},
                        {"type": "section",
                         "text": {"type": "mrkdwn", "text": "Permanently delete this poll and its message."},
                         "accessory": {"type": "button",
                                       "text": {"type": "plain_text", "text": "Delete", "emoji": True},
                                       "style": "danger", "action_id": "delete_poll_from_settings"}}
                    ]
                }

            async with httpx.AsyncClient() as client:
                await client.post("https://slack.com/api/views.open", headers=headers,
                                  json={"trigger_id": data["trigger_id"], "view": view})

        # --- Handle opening the edit modal (from settings modal) ---
        elif action_id == "edit_poll_content":
            poll = polls.find_one({"_id": poll_id_from_modal_metadata})
            if not poll or poll.get("creator_id") != user_id: return Response(status_code=200)

            choices_str = "\n".join(poll.get("choices", []))
            edit_modal_view = {
                "type": "modal", "callback_id": "submit_edit_poll_modal",
                "private_metadata": json.dumps({"poll_id": str(poll_id_from_modal_metadata)}),
                "title": {"type": "plain_text", "text": "Edit Poll"},
                "submit": {"type": "plain_text", "text": "Save"},
                "close": {"type": "plain_text", "text": "Cancel"},
                "blocks": [
                    {"type": "context", "elements": [
                        {"type": "mrkdwn", "text": "⚠️ *Warning:* Editing the poll will reset all existing votes."}]},
                    {"type": "input", "block_id": "edit_question_block",
                     "label": {"type": "plain_text", "text": "Poll Question"},
                     "element": {"type": "plain_text_input", "action_id": "edit_question_input",
                                 "initial_value": poll.get("question", "")}},
                    {"type": "input", "block_id": "edit_choices_block",
                     "label": {"type": "plain_text", "text": "Choices (one per line)"},
                     "element": {"type": "plain_text_input", "action_id": "edit_choices_input", "multiline": True,
                                 "initial_value": choices_str}}
                ]
            }
            headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
            async with httpx.AsyncClient() as client:
                await client.post("https://slack.com/api/views.update", headers=headers,
                                  json={"view_id": data["view"]["id"], "hash": data["view"]["hash"],
                                        "view": edit_modal_view})

        # --- Handle poll deletion (from settings modal) ---
        elif action_id == "delete_poll_from_settings":
            poll = polls.find_one({"_id": poll_id_from_modal_metadata})
            if not poll or poll.get("creator_id") != user_id: return Response(status_code=200)

            headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
            async with httpx.AsyncClient() as client:
                delete_response = await client.post("https://slack.com/api/chat.delete", headers=headers,
                                                    json={"channel": poll["channel"], "ts": poll["message_ts"]})
                if delete_response.status_code == 200 and delete_response.json().get("ok"):
                    polls.delete_one({"_id": poll["_id"]})
                    print(f"Poll message {poll['message_ts']} deleted from Slack and DB.")
                    # Show a success confirmation and close the modal
                    success_view = {
                        "type": "modal", "title": {"type": "plain_text", "text": "Deleted"},
                        "close": {"type": "plain_text", "text": "Close"},
                        "blocks": [
                            {"type": "section", "text": {"type": "plain_text", "text": "Poll deleted successfully."}}]
                    }
                    await client.post("https://slack.com/api/views.update", headers=headers,
                                      json={"view_id": data["view"]["id"], "hash": data["view"]["hash"],
                                            "view": success_view})
                else:
                    print(f"Failed to delete Slack message: {delete_response.text}")
                    # Optionally, update modal to show an error here

    return Response(status_code=200)
