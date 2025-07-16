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


async def update_all_poll_messages(poll_id: ObjectId, client: httpx.AsyncClient):
    """
    Fetches a poll by its ID, rebuilds the Slack message,
    and updates all associated messages in Slack.
    """
    poll = polls.find_one({"_id": poll_id})
    if not poll:
        print(f"Cannot update messages for poll {poll_id}: Poll not found.")
        return

    # --- Calculate Vote Counts ---
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
    allow_others_to_add = poll.get("allow_others_to_add_options", False)
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
        choice_id = str(choice.get("_id", ObjectId()))  # Each choice must have a unique ID.
        voters = choice.get("voters", [])
        vote_count = len(voters)
        current_emoji = emoji_list[i] if i < len(emoji_list) else "🔘"

        percentage_base = total_individual_votes_cast if allow_multiple else total_respondents
        percentage = (vote_count / percentage_base * 100) if percentage_base > 0 else 0
        mention_text = " ".join(f"<@{uid}>" for uid in voters) if voters else ""

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"{current_emoji} *{choice_text}* `{vote_count}` {percentage:.0f}%  \n{mention_text}"},
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": current_emoji},
                "value": choice_id,  # Use the unique choice ID as the button value.
                "action_id": "vote_for_choice"  # Use a generic action ID for all vote buttons.
            }
        })

    if allow_others_to_add:
        blocks.append({
            "type": "actions",
            "elements": [{
                "type": "button",
                "text": {"type": "plain_text", "text": "Add option", "emoji": True},
                "value": str(poll["_id"]),
                "action_id": "open_add_option_modal"
            }]
        })

    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": f"*Total votes:* {total_individual_votes_cast}"},
            {"type": "mrkdwn", "text": f"Created by <@{creator_id}>"}
        ]
    })

    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
    messages_to_update = poll.get("messages", [])
    for msg_info in messages_to_update:
        channel = msg_info.get("channel")
        message_ts = msg_info.get("ts")
        if channel and message_ts:
            await client.post("https://slack.com/api/chat.update", headers=headers, json={
                "channel": channel, "ts": message_ts, "blocks": blocks, "text": question
            })


async def send_poll_to_channels(question, choices_data, channels, poll_id):
    """Sends the initial poll message to multiple Slack channels."""
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
    poll_doc = polls.find_one({"_id": poll_id})
    allow_multiple_votes = poll_doc.get("allow_multiple_votes", False)
    allow_others_to_add = poll_doc.get("allow_others_to_add_options", False)

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
        choice_id = str(choice.get("_id"))  # Get the choice ID.
        current_emoji = emoji_list[i] if i < len(emoji_list) else "🔘"
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{current_emoji} *{choice_text}*"},
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": current_emoji},
                "value": choice_id,  # Use the unique choice ID as the button value.
                "action_id": "vote_for_choice"
            }
        })

    if allow_others_to_add:
        blocks.append({
            "type": "actions",
            "elements": [{
                "type": "button",
                "text": {"type": "plain_text", "text": "Add option", "emoji": True},
                "value": str(poll_id),
                "action_id": "open_add_option_modal"
            }]
        })

    async with httpx.AsyncClient() as client:
        for channel in channels:
            r = await client.post("https://slack.com/api/chat.postMessage", headers=headers,
                                  json={"channel": channel, "blocks": blocks})
            if r.status_code == 200 and r.json().get("ok"):
                response_data = r.json()
                ts = response_data["ts"]
                channel_id = response_data["channel"]
                polls.update_one(
                    {"_id": poll_id},
                    {"$push": {"messages": {"channel": channel_id, "ts": ts}}}
                )
            else:
                print(f"Error sending poll to channel {channel}: {r.status_code} {r.text}")


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

        def extract_choices_from_state(state):
            choices = []
            sorted_blocks = sorted(state.items(), key=lambda item: item[0])
            for block_id, block_data in sorted_blocks:
                if block_id.startswith("choice_block_"):
                    action_id = list(block_data.keys())[0]
                    choice_text = block_data[action_id].get("value")
                    if choice_text:
                        choices.append(choice_text.strip())
            return choices

        if callback_id == "submit_poll_modal":
            question = view_state["question_block"]["question_input"]["value"]
            choices_text = extract_choices_from_state(view_state)
            channels = view_state["channel_block"]["channels_input"]["selected_conversations"]

            selected_options = view_state.get("settings_block", {}).get("settings_checkboxes", {}).get(
                "selected_options", [])
            selected_values = {opt['value'] for opt in selected_options}
            allow_multiple_votes = 'allow_multiple' in selected_values
            allow_others_to_add_options = 'allow_others_to_add' in selected_values

            choices = [{"_id": ObjectId(), "text": text, "voters": []} for text in choices_text if text]

            if not question or not choices or not channels:
                errors = {}
                if not question: errors["question_block"] = "A question is required."
                if not choices: errors["choice_block_0"] = "At least one option is required."
                if not channels: errors["channel_block"] = "At least one channel must be selected."
                return JSONResponse(content={"response_action": "errors", "errors": errors})

            poll_doc = {
                "question": question, "choices": choices, "channels": channels,
                "creator_id": user_id, "messages": [],
                "allow_multiple_votes": allow_multiple_votes,
                "allow_others_to_add_options": allow_others_to_add_options
            }
            result = polls.insert_one(poll_doc)
            asyncio.create_task(send_poll_to_channels(question, choices, channels, result.inserted_id))
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

            selected_options = view_state.get("settings_block", {}).get("settings_checkboxes", {}).get(
                "selected_options", [])
            selected_values = {opt['value'] for opt in selected_options}
            allow_others_to_add_options = 'allow_others_to_add' in selected_values

            old_choices_data = poll.get("choices", [])
            new_choices_data = []
            for i, new_text in enumerate(new_choices_text):
                if i < len(old_choices_data):
                    choice_id = old_choices_data[i].get("_id", ObjectId())
                    voters = old_choices_data[i].get("voters", [])
                    new_choices_data.append({"_id": choice_id, "text": new_text, "voters": voters})
                else:
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

        elif callback_id == "submit_add_option_modal":
            private_metadata = json.loads(data["view"]["private_metadata"])
            poll_id = ObjectId(private_metadata["poll_id"])
            new_option_text = view_state["new_option_block"]["new_option_input"]["value"]

            if not new_option_text:
                return JSONResponse(
                    content={"response_action": "errors",
                             "errors": {"new_option_block": "Option text cannot be empty."}})

            vote_for_it = bool(
                view_state.get("vote_for_option_block", {}).get("vote_for_option_checkbox", {}).get("selected_options"))
            voters = [user_id] if vote_for_it else []
            new_choice = {"_id": ObjectId(), "text": new_option_text.strip(), "voters": voters}

            poll = polls.find_one({"_id": poll_id})
            if poll:
                if vote_for_it and not poll.get("allow_multiple_votes", False):
                    polls.update_one({"_id": poll_id}, {"$pull": {"choices.$[].voters": user_id}})

                polls.update_one({"_id": poll_id}, {"$push": {"choices": new_choice}})

                async with httpx.AsyncClient() as client:
                    await update_all_poll_messages(poll_id, client)

            return Response(status_code=200)

    elif interaction_type == "block_actions":
        action = data["actions"][0]
        action_id = action["action_id"]

        if action_id == "open_create_poll_modal":
            trigger_id = data.get("trigger_id")
            # The channel_id from App Home interaction is the DM with the app
            channel_id = data.get("channel", {}).get("id")

            modal = {
                "trigger_id": trigger_id,
                "view": {
                    "type": "modal",
                    "callback_id": "submit_poll_modal",
                    "title": {"type": "plain_text", "text": "Create a Poll"},
                    "submit": {"type": "plain_text", "text": "Create"},
                    "close": {"type": "plain_text", "text": "Cancel"},
                    "blocks": [
                        {"type": "input", "block_id": "question_block", "label": {"type": "plain_text", "text": "Poll Question"}, "element": {"type": "plain_text_input", "action_id": "question_input", "placeholder": {"type": "plain_text", "text": "What do you want to ask?"}}},
                        {"type": "input", "block_id": "choice_block_0", "label": {"type": "plain_text", "text": "Option 1"}, "element": {"type": "plain_text_input", "action_id": "choice_input_0", "placeholder": {"type": "plain_text", "text": "Write something"}}},
                        {"type": "input", "block_id": "choice_block_1", "optional": True, "label": {"type": "plain_text", "text": "Option 2 (optional)"}, "element": {"type": "plain_text_input", "action_id": "choice_input_1", "placeholder": {"type": "plain_text", "text": "Write something"}}},
                        {"type": "actions", "block_id": "add_option_section", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "Add another option"}, "action_id": "add_option_to_modal"}]},
                        {"type": "input", "block_id": "settings_block", "optional": True, "label": {"type": "plain_text", "text": "Settings (optional)"}, "element": {"type": "checkboxes", "action_id": "settings_checkboxes", "options": [{"text": {"type": "plain_text", "text": "Allow multiple votes"}, "value": "allow_multiple"}, {"text": {"type": "plain_text", "text": "Allow others to add options"}, "value": "allow_others_to_add"}]}},
                        {"type": "input", "block_id": "channel_block", "label": {"type": "plain_text", "text": "Select channel(s) to post"}, "element": {"type": "multi_conversations_select", "action_id": "channels_input", "initial_conversations": [channel_id] if channel_id else [], "placeholder": {"type": "plain_text", "text": "Select channels..."}}}
                    ]
                }
            }
            headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
            async with httpx.AsyncClient() as client:
                await client.post("https://slack.com/api/views.open", headers=headers, json=modal)
            return Response(status_code=200)

        if action_id == "add_option_to_modal":
            view = data["view"]
            blocks = view["blocks"]
            choice_count = sum(1 for b in blocks if b.get("block_id", "").startswith("choice_block_"))
            new_option_num = choice_count + 1

            new_input_block = {
                "type": "input",
                "block_id": f"choice_block_{choice_count}",
                "optional": True,
                "label": {"type": "plain_text", "text": f"Option {new_option_num} (optional)"},
                "element": {"type": "plain_text_input", "action_id": f"choice_input_{choice_count}",
                            "placeholder": {"type": "plain_text", "text": "Write something"}}
            }
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
                    "view_id": view["id"], "hash": view["hash"],
                    "view": {
                        "type": "modal", "callback_id": view["callback_id"],
                        "private_metadata": view["private_metadata"], "title": view["title"],
                        "submit": view["submit"], "close": view["close"], "blocks": blocks
                    }
                })
            return Response(status_code=200)

        if action_id == "vote_for_choice":
            message_ts = data["message"]["ts"]
            channel_id = data["channel"]["id"]
            selected_choice_id = ObjectId(action["value"])
            poll = polls.find_one({"messages": {"$elemMatch": {"ts": message_ts, "channel": channel_id}}})

            if poll:
                allow_multiple = poll.get("allow_multiple_votes", False)
                voter_query = {"choices": {"$elemMatch": {"_id": selected_choice_id, "voters": user_id}}}
                already_voted = polls.find_one({"_id": poll["_id"], **voter_query})

                if not allow_multiple:
                    polls.update_one({"_id": poll["_id"]}, {"$pull": {"choices.$[].voters": user_id}})
                    if not already_voted:
                        polls.update_one(
                            {"_id": poll["_id"], "choices._id": selected_choice_id},
                            {"$push": {"choices.$.voters": user_id}}
                        )
                else:
                    if already_voted:
                        polls.update_one(
                            {"_id": poll["_id"], "choices._id": selected_choice_id},
                            {"$pull": {"choices.$.voters": user_id}}
                        )
                    else:
                        polls.update_one(
                            {"_id": poll["_id"], "choices._id": selected_choice_id},
                            {"$addToSet": {"choices.$.voters": user_id}}
                        )

                async with httpx.AsyncClient() as client:
                    await update_all_poll_messages(poll["_id"], client)

        elif action_id == "open_add_option_modal":
            poll_id_str = action["value"]
            trigger_id = data["trigger_id"]
            view = {
                "type": "modal", "callback_id": "submit_add_option_modal",
                "private_metadata": json.dumps({"poll_id": poll_id_str}),
                "title": {"type": "plain_text", "text": "Add option to poll"},
                "submit": {"type": "plain_text", "text": "Add"}, "close": {"type": "plain_text", "text": "Cancel"},
                "blocks": [
                    {"type": "input", "block_id": "new_option_block",
                     "label": {"type": "plain_text", "text": "New option"},
                     "element": {"type": "plain_text_input", "action_id": "new_option_input",
                                 "placeholder": {"type": "plain_text", "text": "Your option"}}},
                    {"type": "section", "text": {"type": "mrkdwn",
                                                 "text": "Your option will be added to the poll for everyone to vote on."}},
                    {"type": "input", "optional": True, "block_id": "vote_for_option_block",
                     "label": {"type": "plain_text", "text": " "},
                     "element": {"type": "checkboxes", "action_id": "vote_for_option_checkbox",
                                 "options": [{"text": {"type": "plain_text", "text": "Vote for this option"},
                                              "value": "vote_now"}]}}
                ]
            }
            headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
            async with httpx.AsyncClient() as client:
                await client.post("https://slack.com/api/views.open", headers=headers,
                                  json={"trigger_id": trigger_id, "view": view})

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
                        {"type": "section",
                         "text": {"type": "mrkdwn", "text": f"*Question:* {poll.get('question', 'N/A')}"}},
                        {"type": "context",
                         "elements": [{"type": "mrkdwn", "text": f"Created by <@{poll.get('creator_id', 'N/A')}>"}]},
                        {"type": "divider"},
                        {"type": "section", "text": {"type": "mrkdwn", "text": "*Admin controls*"}},
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

            initial_settings = []
            if poll.get("allow_others_to_add_options"):
                initial_settings.append({"text": {"type": "plain_text", "text": "Allow others to add options"},
                                         "value": "allow_others_to_add"})

            settings_block = {
                "type": "input", "block_id": "settings_block", "optional": True,
                "label": {"type": "plain_text", "text": "Settings"},
                "element": {
                    "type": "checkboxes", "action_id": "settings_checkboxes",
                    "options": [{"text": {"type": "plain_text", "text": "Allow others to add options"},
                                 "value": "allow_others_to_add"}]
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
                                  json={"view_id": data["view"]["id"], "hash": data["view"]["hash"],
                                        "view": edit_modal_view})

        elif action_id == "delete_poll_from_settings":
            private_metadata = json.loads(data["view"]["private_metadata"])
            poll_id = ObjectId(private_metadata["poll_id"])
            poll = polls.find_one({"_id": poll_id})
            if not poll or poll.get("creator_id") != user_id: return Response(status_code=200)

            headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
            async with httpx.AsyncClient() as client:
                for msg_info in poll.get("messages", []):
                    await client.post("https://slack.com/api/chat.delete", headers=headers,
                                      json={"channel": msg_info["channel"], "ts": msg_info["ts"]})

                polls.delete_one({"_id": poll["_id"]})
                success_view = {
                    "type": "modal", "title": {"type": "plain_text", "text": "Deleted"},
                    "close": {"type": "plain_text", "text": "Close"},
                    "blocks": [{"type": "section",
                                "text": {"type": "plain_text", "text": "Poll deleted successfully from all channels."}}]
                }
                await client.post("https://slack.com/api/views.update", headers=headers,
                                  json={"view_id": data["view"]["id"], "hash": data["view"]["hash"],
                                        "view": success_view})

    return Response(status_code=200)