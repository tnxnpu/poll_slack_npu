# interactions.py
import asyncio
import json

import httpx
from bson.objectid import ObjectId
from fastapi import APIRouter, Request, Form
from fastapi.responses import JSONResponse, Response

from db import polls
from settings import SLACK_BOT_TOKEN

interactions_router = APIRouter()


# --- NEW: Command handler for the initial /poll command ---
@interactions_router.post("/slack/commands/poll")
async def create_poll_modal(channel_id: str = Form(...), trigger_id: str = Form(...)):
    """
    Handles the initial `/poll` slash command and opens the poll creation modal.
    """
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}

    # This is the new, dynamic modal structure
    initial_modal_view = {
        "type": "modal",
        "callback_id": "submit_poll_modal",
        "private_metadata": channel_id,
        "title": {"type": "plain_text", "text": "Create a Poll"},
        "submit": {"type": "plain_text", "text": "Create"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "question_block",
                "label": {"type": "plain_text", "text": "Poll Question"},
                "element": {"type": "plain_text_input", "action_id": "question_input",
                            "placeholder": {"type": "plain_text", "text": "What do you want to ask?"}}
            },
            {
                "type": "input",
                "block_id": "choice_block_0",
                "label": {"type": "plain_text", "text": "Option 1"},
                "element": {"type": "plain_text_input", "action_id": "choice_input_0"}
            },
            {
                "type": "input",
                "block_id": "choice_block_1",
                "label": {"type": "plain_text", "text": "Option 2 (optional)"},
                "element": {"type": "plain_text_input", "action_id": "choice_input_1"}
            },
            {
                "type": "actions",
                "block_id": "add_option_section",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Add another option"},
                        "action_id": "add_option_to_modal"
                    }
                ]
            },
            {
                "type": "input",
                "block_id": "settings_block",
                "optional": True,
                "label": {"type": "plain_text", "text": "Settings (optional)"},
                "element": {
                    "type": "checkboxes",
                    "action_id": "allow_multiple_votes_checkbox",
                    "options": [
                        {
                            "text": {"type": "plain_text", "text": "Allow multiple votes"},
                            "value": "allow_multiple"
                        }
                    ]
                }
            }
        ]
    }

    async with httpx.AsyncClient() as client:
        await client.post("https://slack.com/api/views.open", headers=headers, json={
            "trigger_id": trigger_id,
            "view": initial_modal_view
        })

    return Response(status_code=200)


async def update_slack_poll_message(poll_id: ObjectId, client: httpx.AsyncClient):
    """
    Fetches a poll by its ID, rebuilds the Slack message using the new data structure,
    and updates the message in Slack.
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

    # --- Calculate Vote Counts from the new structure ---
    total_individual_votes_cast = sum(len(choice.get("voters", [])) for choice in poll.get("choices", []))
    unique_voters = set()
    for choice in poll.get("choices", []):
        for voter in choice.get("voters", []):
            unique_voters.add(voter)
    total_respondents = len(unique_voters)

    # --- Build Slack Message Blocks ---
    question = poll["question"]
    choices_data = poll.get("choices", [])
    creator_id = poll.get("creator_id", "unknown")
    allow_multiple = poll.get("allow_multiple_votes", False)
    emoji_list = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{question}*"},
            "accessory": {
                "type": "overflow",
                "options": [{"text": {"type": "plain_text", "text": "Settings", "emoji": True},
                             "value": f"settings_{poll['_id']}"}],
                "action_id": "open_poll_settings"
            }
        }
    ]

    if allow_multiple:
        blocks.append(
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "💡 _You may vote for multiple options_"}]})

    for i, choice in enumerate(choices_data):
        choice_text = choice.get("text", "N/A")
        voters = choice.get("voters", [])
        vote_count = len(voters)
        current_emoji = emoji_list[i] if i < len(emoji_list) else "🔘"

        percentage_base = total_individual_votes_cast if allow_multiple else total_respondents
        percentage = (vote_count / percentage_base * 100) if percentage_base > 0 else 0
        mention_text = " ".join(f"<@{uid}>" for uid in voters) if voters else ""

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"{current_emoji} *{choice_text}* | *{percentage:.0f}%* `{vote_count}`\n{mention_text}"},
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": current_emoji},
                "value": choice_text,
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

    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
    await client.post("https://slack.com/api/chat.update", headers=headers, json={
        "channel": channel, "ts": message_ts, "blocks": blocks, "text": question
    })


async def send_poll_to_slack(question, choices_data, channel, poll_id):
    """Sends the initial poll message to Slack."""
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
    poll_doc = polls.find_one({"_id": poll_id})
    allow_multiple_votes = poll_doc.get("allow_multiple_votes", False)

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
    for i, choice in enumerate(choices_data):
        choice_text = choice.get("text", "N/A")
        current_emoji = emoji_list[i] if i < len(emoji_list) else "🔘"
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{current_emoji} *{choice_text}*"},
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": current_emoji},
                "value": choice_text,
                "action_id": f"vote_option_{i}"
            }
        })

    async with httpx.AsyncClient() as client:
        r = await client.post("https://slack.com/api/chat.postMessage", headers=headers,
                              json={"channel": channel, "blocks": blocks})
        if r.status_code == 200 and r.json().get("ok"):
            ts = r.json()["ts"]
            polls.update_one({"_id": poll_id}, {"$set": {"message_ts": ts}})
        else:
            print(f"Error sending poll to slack: {r.status_code} {r.text}")


@interactions_router.post("/slack/interactions")
async def handle_interactions(request: Request):
    """Handles all incoming Slack interactions, including dynamic modal updates."""
    payload = await request.form()
    data = json.loads(payload.get("payload"))
    interaction_type = data["type"]
    user_id = data["user"]["id"]

    if interaction_type == "view_submission":
        callback_id = data["view"]["callback_id"]
        view_state = data["view"]["state"]["values"]

        # --- Helper function to extract choices from dynamic inputs ---
        def extract_choices_from_state(state):
            choices = []
            # Sort by block_id to maintain order
            sorted_blocks = sorted(state.items(), key=lambda item: item[0])
            for block_id, block_data in sorted_blocks:
                if block_id.startswith("choice_block_"):
                    # The action_id within the block holds the value
                    action_id = list(block_data.keys())[0]
                    choice_text = block_data[action_id].get("value")
                    if choice_text:
                        choices.append(choice_text.strip())
            return choices

        if callback_id == "submit_poll_modal":
            question = view_state["question_block"]["question_input"]["value"]
            choices_text = extract_choices_from_state(view_state)

            channel = data["view"]["private_metadata"]
            allow_multiple_votes = bool(
                view_state.get("settings_block", {}).get("allow_multiple_votes_checkbox", {}).get("selected_options"))

            choices = [{"text": text, "voters": []} for text in choices_text]

            poll_doc = {
                "question": question, "choices": choices, "channel": channel,
                "creator_id": user_id, "message_ts": None,
                "allow_multiple_votes": allow_multiple_votes
            }
            result = polls.insert_one(poll_doc)
            asyncio.create_task(send_poll_to_slack(question, choices, channel, result.inserted_id))
            return Response(status_code=200)

        elif callback_id == "submit_edit_poll_modal":
            private_metadata = json.loads(data["view"]["private_metadata"])
            poll_id = ObjectId(private_metadata["poll_id"])
            poll = polls.find_one({"_id": poll_id})

            if not poll or poll.get("creator_id") != user_id:
                return JSONResponse(
                    content={"response_action": "errors", "errors": {"question_block": "You are not authorized."}})

            new_question = view_state["question_block"]["question_input"]["value"]
            new_choices_text = extract_choices_from_state(view_state)

            old_choices_data = poll.get("choices", [])
            new_choices_data = []
            for i, new_text in enumerate(new_choices_text):
                if i < len(old_choices_data):
                    voters = old_choices_data[i].get("voters", [])
                    new_choices_data.append({"text": new_text, "voters": voters})
                else:
                    new_choices_data.append({"text": new_text, "voters": []})

            polls.update_one({"_id": poll_id}, {"$set": {"question": new_question, "choices": new_choices_data}})
            async with httpx.AsyncClient() as client:
                await update_slack_poll_message(poll_id, client)
            return Response(status_code=200)

    elif interaction_type == "block_actions":
        action = data["actions"][0]
        action_id = action["action_id"]

        # --- Handle adding a new option to a poll creation/edit modal ---
        if action_id == "add_option_to_modal":
            view = data["view"]
            blocks = view["blocks"]

            # Count existing choice inputs to determine the next index
            choice_count = sum(1 for b in blocks if b.get("block_id", "").startswith("choice_block_"))
            new_option_num = choice_count + 1

            new_input_block = {
                "type": "input",
                "block_id": f"choice_block_{choice_count}",
                "label": {"type": "plain_text", "text": f"Option {new_option_num}"},
                "element": {"type": "plain_text_input", "action_id": f"choice_input_{choice_count}"}
            }

            # Find the position of the "Add another option" button to insert the new block before it
            insert_pos = -1
            for i, block in enumerate(blocks):
                if block.get("block_id") == "add_option_section":
                    insert_pos = i
                    break

            if insert_pos != -1:
                blocks.insert(insert_pos, new_input_block)

            headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
            async with httpx.AsyncClient() as client:
                await client.post("https://slack.com/api/views.update", headers=headers, json={
                    "view_id": view["id"],
                    "hash": view["hash"],
                    "view": {
                        "type": "modal",
                        "callback_id": view["callback_id"],
                        "private_metadata": view["private_metadata"],
                        "title": view["title"],
                        "submit": view["submit"],
                        "close": view["close"],
                        "blocks": blocks
                    }
                })
            return Response(status_code=200)

        if action_id.startswith("vote_option_"):
            message_ts = data["message"]["ts"]
            channel_id = data["channel"]["id"]
            selected_choice_text = action["value"]
            poll = polls.find_one({"message_ts": message_ts, "channel": channel_id})

            if poll:
                allow_multiple = poll.get("allow_multiple_votes", False)

                if not allow_multiple:
                    # If single-vote, first check if the user is un-voting the currently selected option
                    is_unvoting = False
                    for choice in poll.get("choices", []):
                        if choice.get("text") == selected_choice_text and user_id in choice.get("voters", []):
                            is_unvoting = True
                            break

                    # Pull the user from all choices first
                    polls.update_one({"_id": poll["_id"]}, {"$pull": {"choices.$[].voters": user_id}})

                    # If they were not un-voting, push their vote to the new choice
                    if not is_unvoting:
                        polls.update_one({"_id": poll["_id"], "choices.text": selected_choice_text},
                                         {"$push": {"choices.$.voters": user_id}})
                else:  # Multi-vote logic
                    already_voted = any(
                        c.get("text") == selected_choice_text and user_id in c.get("voters", []) for c in
                        poll.get("choices", []))
                    if already_voted:
                        polls.update_one({"_id": poll["_id"], "choices.text": selected_choice_text},
                                         {"$pull": {"choices.$.voters": user_id}})
                    else:
                        polls.update_one({"_id": poll["_id"], "choices.text": selected_choice_text},
                                         {"$addToSet": {"choices.$.voters": user_id}})

                async with httpx.AsyncClient() as client:
                    await update_slack_poll_message(poll["_id"], client)

        elif action_id == "open_poll_settings":
            poll_id_str = action["selected_option"]["value"].split("_")[1]
            poll_id = ObjectId(poll_id_str)
            poll = polls.find_one({"_id": poll_id})
            if not poll: return Response(status_code=200)

            headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
            view = {}
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
                        {"type": "section", "text": {"type": "mrkdwn", "text": "Permanently delete this poll."},
                         "accessory": {"type": "button",
                                       "text": {"type": "plain_text", "text": "Delete", "emoji": True},
                                       "style": "danger", "action_id": "delete_poll_from_settings"}}
                    ]
                }
            async with httpx.AsyncClient() as client:
                await client.post("https://slack.com/api/views.open", headers=headers,
                                  json={"trigger_id": data["trigger_id"], "view": view})

        elif action_id == "edit_poll_content":
            private_metadata = json.loads(data["view"]["private_metadata"])
            poll_id = ObjectId(private_metadata["poll_id"])
            poll = polls.find_one({"_id": poll_id})
            if not poll or poll.get("creator_id") != user_id: return Response(status_code=200)

            # --- Build the dynamic edit modal ---
            edit_blocks = [
                {"type": "input", "block_id": "question_block",
                 "label": {"type": "plain_text", "text": "Poll Question"},
                 "element": {"type": "plain_text_input", "action_id": "question_input",
                             "initial_value": poll.get("question", "")}}
            ]
            for i, choice in enumerate(poll.get("choices", [])):
                edit_blocks.append({
                    "type": "input", "block_id": f"choice_block_{i}",
                    "label": {"type": "plain_text", "text": f"Option {i + 1}"},
                    "element": {"type": "plain_text_input", "action_id": f"choice_input_{i}",
                                "initial_value": choice.get("text", "")}
                })

            edit_blocks.append({
                "type": "actions", "block_id": "add_option_section",
                "elements": [{"type": "button", "text": {"type": "plain_text", "text": "Add another option"},
                              "action_id": "add_option_to_modal"}]
            })

            edit_modal_view = {
                "type": "modal", "callback_id": "submit_edit_poll_modal",
                "private_metadata": json.dumps({"poll_id": str(poll_id)}),
                "title": {"type": "plain_text", "text": "Edit Poll"},
                "submit": {"type": "plain_text", "text": "Save"},
                "close": {"type": "plain_text", "text": "Cancel"},
                "blocks": edit_blocks
            }
            headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
            async with httpx.AsyncClient() as client:
                await client.post("https://slack.com/api/views.update", headers=headers,
                                  json={"view_id": data["view"]["id"], "hash": data["view"]["hash"],
                                        "view": edit_modal_view})

        elif action_id == "delete_poll_from_settings":
            private_metadata = json.loads(data["view"]["private_metadata"])
            poll_id = ObjectId(private_metadata["poll_id"])
            poll = polls.find_one({"_id": poll_id})
            if not poll or poll.get("creator_id") != user_id: return Response(status_code=200)

            headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
            async with httpx.AsyncClient() as client:
                delete_response = await client.post("https://slack.com/api/chat.delete", headers=headers,
                                                    json={"channel": poll["channel"], "ts": poll["message_ts"]})
                if delete_response.status_code == 200 and delete_response.json().get("ok"):
                    polls.delete_one({"_id": poll["_id"]})
                    success_view = {
                        "type": "modal", "title": {"type": "plain_text", "text": "Deleted"},
                        "close": {"type": "plain_text", "text": "Close"},
                        "blocks": [
                            {"type": "section", "text": {"type": "plain_text", "text": "Poll deleted successfully."}}]
                    }
                    await client.post("https://slack.com/api/views.update", headers=headers,
                                      json={"view_id": data["view"]["id"], "hash": data["view"]["hash"],
                                            "view": success_view})

    return Response(status_code=200)
