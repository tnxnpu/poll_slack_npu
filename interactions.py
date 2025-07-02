# interactions.py
import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response, JSONResponse  # Import JSONResponse
import json
import httpx
from db import polls
from settings import SLACK_BOT_TOKEN
from bson.objectid import ObjectId

interactions_router = APIRouter()


async def send_poll_to_slack(question, choices, channel, poll_id):
    """
    Sends the poll message to Slack with the given question and choices.
    Stores the message timestamp (ts) in the database for later updates.
    The buttons will now display numbers (1, 2, 3, ...) instead of the full choice text.
    """
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json"
    }

    # Fetch the poll from DB to get 'allow_multiple_votes' setting and creator_id
    # This is needed here to conditionally display the notice and in handle_interactions for permission checks
    poll_doc = polls.find_one({"_id": poll_id})
    allow_multiple_votes = poll_doc.get("allow_multiple_votes", False) if poll_doc else False
    creator_id_from_db = poll_doc.get("creator_id") if poll_doc else None

    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"* {question}*"},
            "accessory": {  # This overflow button is now always present for all viewers
                "type": "overflow",
                "options": [
                    {
                        "text": {"type": "plain_text", "text": "Settings", "emoji": True},
                        "value": f"settings_{poll_id}"  # Pass poll_id for identification
                    }
                ],
                "action_id": "open_poll_settings"
            }
        }
    ]

    if allow_multiple_votes:
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "💡 _You may vote for multiple options_"}
            ]
        })

    emoji_list = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

    # Add choice buttons with emojis and the actual choice text next to them
    # The button itself will display an emoji, but its 'value' will be the actual choice string
    for i, choice in enumerate(choices):
        current_emoji = emoji_list[i] if i < len(emoji_list) else "🔘"
        # The display format for initial poll (no votes yet)
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{current_emoji} *{choice}*"  # Emoji before choice text
            },
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": current_emoji},  # Button with emoji
                "value": choice,
                "action_id": f"vote_option_{i}"
            }
        })

    message = {
        "channel": channel,
        "blocks": blocks
    }

    async with httpx.AsyncClient() as client:
        r = await client.post("https://slack.com/api/chat.postMessage", headers=headers, json=message)
        print("📤 Poll sent:", r.status_code, r.text)

        if r.status_code == 200 and r.json().get("ok"):
            ts = r.json()["ts"]
            # Store the message timestamp (ts) and original creator ID in the database for future updates
            polls.update_one({"_id": poll_id},
                             {"$set": {"message_ts": ts, "creator_id": creator_id_from_db}})  # Ensure creator_id is set


@interactions_router.post("/slack/interactions")
async def handle_interactions(request: Request):
    """
    Handles incoming Slack interactions, including modal submissions and button clicks.
    """
    payload = await request.form()
    raw = payload.get("payload")
    data = json.loads(raw)

    print(f"\n--- Incoming Interaction Payload ---")
    print(f"Type: {data['type']}")
    if 'actions' in data:
        print(f"Action ID: {data['actions'][0].get('action_id')}")
        print(f"Action Value: {data['actions'][0].get('value')}")
    print(f"User ID: {data['user']['id']}")
    print(f"Channel ID: {data.get('channel', {}).get('id')}")
    print(f"Message TS: {data.get('message', {}).get('ts')}")
    print(f"View ID: {data.get('view', {}).get('id')}")  # Added print for view_id
    print(f"Callback ID: {data.get('view', {}).get('callback_id')}")  # Added print for callback_id
    print(f"------------------------------------")

    # === Modal submitted (view_submission) ===
    if data["type"] == "view_submission":
        if data["view"]["callback_id"] == "submit_poll_modal":
            view_state = data["view"]["state"]["values"]
            question = view_state["question_block"]["question_input"]["value"]
            choices_raw = view_state["choices_block"]["choices_input"]["value"]
            channel = data["view"]["private_metadata"]  # Channel where the poll will be posted
            creator_id = data["user"]["id"]  # User who created the poll

            # Determine if multiple votes are allowed based on checkbox state
            allow_multiple_votes = False
            if "multiple_votes_block" in view_state and \
                    "allow_multiple_votes_checkbox" in view_state["multiple_votes_block"] and \
                    view_state["multiple_votes_block"]["allow_multiple_votes_checkbox"].get("selected_options"):
                allow_multiple_votes = True

            # Clean and prepare choices
            choices = [c.strip() for c in choices_raw.strip().split("\n") if c.strip()]

            # Prepare the poll document for MongoDB
            poll_doc = {
                "question": question,
                "choices": choices,
                "channel": channel,
                "creator_id": creator_id,
                "message_ts": None,  # Will be updated after the message is sent to Slack
                "votes": {},  # Stores user_id: [selected_choices] pairs for multiple, or string for single
                "allow_multiple_votes": allow_multiple_votes  # Store setting in DB
            }

            # Insert the new poll into the database
            result = polls.insert_one(poll_doc)
            poll_id = result.inserted_id

            print(f"Poll inserted to DB: {poll_id}")

            # Send the poll to Slack asynchronously
            asyncio.create_task(send_poll_to_slack(question, choices, channel, poll_id))
            return Response(status_code=200)  # Always acknowledge modal submission

        # --- Removed delete_confirm_modal handling as it's no longer used ---
        return Response(status_code=200)  # Acknowledge any other modal submission (e.g., close)

    # === Block actions (button clicks) ===
    elif data["type"] == "block_actions":
        user_id = data["user"]["id"]  # User who clicked the button
        action_id = data["actions"][0]["action_id"]  # Get the action_id to distinguish between vote and delete

        # Determine if this block_action originated from a message or a modal
        is_from_message = "message" in data
        is_from_modal = "view" in data and data["view"].get(
            "callback_id") == "poll_settings_modal"  # Only check for settings modal now

        message_ts = None
        channel = None
        poll_id_from_modal_metadata = None

        if is_from_message:
            message_ts = data["message"].get("ts")
            channel = data["channel"].get("id")
        elif is_from_modal:
            # If from modal, get poll_id from private_metadata
            private_metadata = json.loads(data["view"]["private_metadata"])
            poll_id_from_modal_metadata = ObjectId(private_metadata["poll_id"])
            # We don't have message_ts or channel directly from modal action, need to fetch poll

        # --- Handle Overflow Button Click (opens modal) ---
        if action_id == "open_poll_settings":
            # This action originates from a message, so message_ts and channel are available
            if not message_ts or not channel:
                print(f"Missing message_ts or channel for open_poll_settings action. Cannot proceed.")
                return Response(status_code=200)

            # Correctly extract poll_id from the value of the selected option
            poll_id_str = data["actions"][0]["selected_option"]["value"].split("_")[1]
            poll_id = ObjectId(poll_id_str)

            # Fetch the poll to get creator_id for permission check and poll details for modal
            poll_details = polls.find_one({"_id": poll_id})

            if not poll_details:
                print(f"Poll {poll_id} not found when trying to open settings.")
                return Response(status_code=200)

            if poll_details.get("creator_id") != user_id:
                # User is NOT the creator, show informational modal
                poll_info_modal = {
                    "trigger_id": data["trigger_id"],
                    "view": {
                        "type": "modal",
                        "callback_id": "poll_info_modal",
                        "title": {"type": "plain_text", "text": "Poll Information"},
                        "close": {"type": "plain_text", "text": "Close"},
                        "blocks": [
                            {
                                "type": "section",
                                "text": {"type": "mrkdwn", "text": f"*Question:* {poll_details.get('question', 'N/A')}"}
                            },
                            {
                                "type": "context",
                                "elements": [
                                    {"type": "mrkdwn", "text": f"Created by <@{poll_details.get('creator_id', 'N/A')}>"}
                                ]
                            }
                        ]
                    }
                }
                headers = {
                    "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
                    "Content-Type": "application/json"
                }
                async with httpx.AsyncClient() as client:
                    await client.post("https://slack.com/api/views.open", headers=headers, json=poll_info_modal)
                print(f"User {user_id} is not creator. Opened poll info modal for poll {poll_id}.")
                return Response(status_code=200)

            settings_modal = {
                "trigger_id": data["trigger_id"],
                "view": {
                    "type": "modal",
                    "callback_id": "poll_settings_modal",
                    "private_metadata": json.dumps({"poll_id": str(poll_id)}),
                    "title": {"type": "plain_text", "text": "Polly Settings"},
                    "blocks": [
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": "*Admin Controls*"},
                            "block_id": "admin_controls_header"
                        },
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": "Edit the content of this polly"},
                            "accessory": {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Edit poll", "emoji": True},
                                "value": "edit_poll_content",
                                "action_id": "edit_poll_content"
                            },
                            "block_id": "edit_poll_section"
                        },
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn",
                                     "text": "Permanently delete this polly and remove all its messages in Slack"},
                            "accessory": {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Delete", "emoji": True},
                                "style": "danger",
                                "value": "delete_poll_from_settings",
                                "action_id": "delete_poll_from_settings"
                            },
                            "block_id": "delete_poll_section"
                        }
                    ]
                }
            }

            headers = {
                "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
                "Content-Type": "application/json"
            }
            async with httpx.AsyncClient() as client:
                await client.post("https://slack.com/api/views.open", headers=headers, json=settings_modal)
            return Response(status_code=200)

        # --- Handle Delete Poll Button Click from Settings Modal (direct deletion) ---
        elif action_id == "delete_poll_from_settings":
            # Ensure this action came from a modal
            if not is_from_modal:
                print(f"Action '{action_id}' not from modal as expected. Cannot proceed.")
                return Response(status_code=200)

            if not poll_id_from_modal_metadata:
                print(f"Missing poll_id in modal metadata for delete_poll_from_settings action. Cannot proceed.")
                return Response(status_code=200)

            poll = polls.find_one({"_id": poll_id_from_modal_metadata})
            if not poll or poll.get("creator_id") != user_id:
                print(
                    f"User {user_id} tried to delete poll {poll_id_from_modal_metadata} but is not the creator or poll not found.")
                # If not authorized or poll not found, just acknowledge the interaction without closing the modal
                return Response(status_code=200)

            message_ts = poll["message_ts"]
            channel = poll["channel"]
            print(
                f"Attempting to delete poll {poll_id_from_modal_metadata} by {user_id} in channel {channel} with ts {message_ts}")
            headers = {
                "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
                "Content-Type": "application/json"
            }
            try:
                async with httpx.AsyncClient() as client:
                    delete_response = await client.post("https://slack.com/api/chat.delete", headers=headers, json={
                        "channel": channel,
                        "ts": message_ts
                    })
                    if delete_response.status_code == 200 and delete_response.json().get("ok"):
                        polls.delete_one({"_id": poll["_id"]})
                        print(f"Poll message {message_ts} deleted from Slack and DB.")
                        # IMPORTANT: Return this response ONLY on success to close the modal
                        return JSONResponse(content={"response_action": "clear"})
                    else:
                        print(f"Failed to delete Slack message: {delete_response.text}")
                        # If Slack deletion fails, we should still acknowledge the interaction,
                        # but the modal won't close automatically via "clear".
                        # You could optionally update the modal to show an error message here.
                        return Response(status_code=200) # Keep the modal open or show an error
            except httpx.RequestError as e:
                print(f"HTTP request failed during poll deletion: {e}")
                # Handle network errors during the API call
                return Response(status_code=200) # Keep the modal open

        # --- Removed confirm_delete_poll and cancel_delete_poll handlers as no confirmation modal ---

        # --- Handle Edit Poll Button Click from Settings Modal ---
        elif action_id == "edit_poll_content":
            # This action comes from a modal, so poll_id is in private_metadata
            if not poll_id_from_modal_metadata:
                print(f"Missing poll_id in modal metadata for edit_poll_content action. Cannot proceed.")
                return Response(status_code=200)

            poll = polls.find_one({"_id": poll_id_from_modal_metadata})
            if not poll or poll.get("creator_id") != user_id:
                print(
                    f"User {user_id} tried to edit poll {poll_id_from_modal_metadata} but is not the creator or poll not found.")
                return Response(status_code=200)

            print(f"User {user_id} clicked Edit Poll for poll {poll_id_from_modal_metadata}.")
            # Actual "Edit Poll" modal opening/logic would go here
            return Response(status_code=200)


        # --- Handle Vote Button Click ---
        elif action_id.startswith("vote_option_"):  # This catches all vote buttons
            # This action originates from a message, so message_ts and channel are available
            if not message_ts or not channel:
                print(f"Missing message_ts or channel for vote_option action. Cannot proceed.")
                return Response(status_code=200)

            selected_choice = data["actions"][0]["value"]

            # Find the poll in the database using message_ts and channel
            poll_query_result = polls.find_one({"message_ts": message_ts, "channel": channel})

            if poll_query_result:
                allow_multiple = poll_query_result.get("allow_multiple_votes", False)
                creator_id_from_db = poll_query_result.get("creator_id")
                current_user_votes_from_db = poll_query_result.get("votes", {}).get(user_id, None)

                if allow_multiple:
                    current_user_votes_list = current_user_votes_from_db if isinstance(current_user_votes_from_db,
                                                                                       list) else []
                    if selected_choice in current_user_votes_list:
                        polls.update_one(
                            {"_id": poll_query_result["_id"]},
                            {"$pull": {f"votes.{user_id}": selected_choice}}
                        )
                    else:
                        polls.update_one(
                            {"_id": poll_query_result["_id"]},
                            {"$addToSet": {f"votes.{user_id}": selected_choice}}
                        )
                else:
                    if current_user_votes_from_db == selected_choice:
                        polls.update_one(
                            {"_id": poll_query_result["_id"]},
                            {"$unset": {f"votes.{user_id}": ""}}
                        )
                    else:
                        polls.update_one(
                            {"_id": poll_query_result["_id"]},
                            {"$set": {f"votes.{user_id}": selected_choice}}

                        )

                # Re-fetch the poll after update to get the latest state
                poll_updated = polls.find_one({"_id": poll_query_result["_id"]})

                all_votes_flat = {}
                unique_voters = set()
                total_individual_votes_cast = 0

                for uid, user_vote_data in poll_updated.get("votes", {}).items():
                    if user_vote_data:
                        unique_voters.add(uid)
                        if isinstance(user_vote_data, list):
                            for choice_item in user_vote_data:
                                all_votes_flat.setdefault(choice_item, []).append(uid)
                                total_individual_votes_cast += 1
                        else:
                            all_votes_flat.setdefault(user_vote_data, []).append(uid)
                            total_individual_votes_cast += 1

                total_respondents = len(unique_voters)
                display_total_votes_count = total_individual_votes_cast

                choices = poll_updated["choices"]
                question = poll_updated["question"]
                creator_id = poll_updated.get("creator_id", "unknown")

                emoji_list = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "�"]

                blocks = [
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"*{question}*"}
                    }
                ]

                # Add overflow button ONLY if the current user is the creator
                blocks[0]["accessory"] = {
                    "type": "overflow",
                    "options": [
                        {
                            "text": {"type": "plain_text", "text": "Settings", "emoji": True},
                            "value": f"settings_{poll_updated['_id']}"
                        }
                    ],
                    "action_id": "open_poll_settings"
                }

                if allow_multiple:
                    blocks.append({
                        "type": "context",
                        "elements": [
                            {"type": "mrkdwn", "text": "💡 _You may vote for multiple options_"}
                        ]
                    })

                for i, choice in enumerate(choices):
                    current_emoji = emoji_list[i] if i < len(emoji_list) else "🔘"
                    user_ids_for_this_choice = all_votes_flat.get(choice, [])
                    vote_count = len(user_ids_for_this_choice)

                    percentage_base = total_individual_votes_cast if allow_multiple else total_respondents
                    percentage = (vote_count / percentage_base * 100) if percentage_base > 0 else 0

                    mention_text = " ".join(
                        f"<@{uid}>" for uid in user_ids_for_this_choice) if user_ids_for_this_choice else ""

                    blocks.append({
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"{current_emoji} *{choice}* | *{percentage:.0f}%* `{vote_count}`\n{mention_text}"
                        },
                        "accessory": {
                            "type": "button",
                            "text": {"type": "plain_text", "text": current_emoji},
                            "value": choice,
                            "action_id": f"vote_option_{i}"
                        }
                    })

                context_elements_bottom = [
                    {"type": "mrkdwn", "text": f"*Total votes:* {display_total_votes_count}"},
                    {"type": "mrkdwn", "text": f"Created by <@{creator_id}>"}
                ]

                blocks.append({
                    "type": "context",
                    "elements": context_elements_bottom
                })

                print(f"Final Blocks for Slack Update: {json.dumps(blocks, indent=2)}")

                headers = {
                    "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
                    "Content-Type": "application/json"
                }

                async with httpx.AsyncClient() as client:
                    update_response = await client.post("https://slack.com/api/chat.update", headers=headers, json={
                        "channel": channel,
                        "ts": message_ts,
                        "blocks": blocks,
                        "text": question  # Fallback text for notifications
                    })
                print(f"Slack chat.update response status: {update_response.status_code}")
                print(f"Slack chat.update response text: {update_response.text}")

            return Response(status_code=200)

    print(f"--- Unhandled Interaction Type: {data['type']} ---")
    return Response(status_code=200)