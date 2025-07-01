# interactions.py
import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response
import json
import httpx
from db import polls
from settings import SLACK_BOT_TOKEN
from pymongo import ReturnDocument

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

    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"* {question}*"}
        }
    ]

    # --- START OF CHANGED CODE (Added Debugging Prints) ---
    # Fetch the poll from DB to get 'allow_multiple_votes' setting
    poll_doc = polls.find_one({"_id": poll_id})
    allow_multiple_votes = poll_doc.get("allow_multiple_votes", False) if poll_doc else False
    creator_id = poll_doc.get("creator_id") if poll_doc else None  # Get creator ID for overflow button logic
    print(f"send_poll_to_slack: Poll ID: {poll_id}, allow_multiple_votes: {allow_multiple_votes}")

    if allow_multiple_votes:
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "💡 _You may vote for multiple options_"}
            ]
        })
    # --- END OF CHANGED CODE ---

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
            polls.update_one({"_id": poll_id}, {"$set": {"message_ts": ts}})


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
    print(f"------------------------------------")

    # === Modal submitted (when a new poll is created) ===
    if data["type"] == "view_submission":
        view_state = data["view"]["state"]["values"]
        question = view_state["question_block"]["question_input"]["value"]
        choices_raw = view_state["choices_block"]["choices_input"]["value"]
        channel = data["view"]["private_metadata"]  # Channel where the poll will be posted
        creator_id = data["user"]["id"]  # User who created the poll

        # Determine if multiple votes are allowed based on checkbox state (reverted to 'votes' naming)
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
            "votes": {},  # Stores user_id: selected_choice pairs (single vote only now)
            "allow_multiple_votes": allow_multiple_votes  # Store setting in DB (reverted field name)
        }

        # Insert the new poll into the database
        result = polls.insert_one(poll_doc)
        poll_id = result.inserted_id

        print(f"Poll inserted to DB: {poll_id}")

        # Send the poll to Slack asynchronously
        asyncio.create_task(send_poll_to_slack(question, choices, channel, poll_id))
        return Response(status_code=200)

    # === Block actions (button clicks) ===
    elif data["type"] == "block_actions":
        user_id = data["user"]["id"]  # User who clicked the button
        action_id = data["actions"][0]["action_id"]  # Get the action_id to distinguish between vote and delete

        message_ts = data["message"]["ts"]  # Timestamp of the poll message
        channel = data["channel"]["id"]  # Channel where the interaction occurred

        # --- Removed Delete Poll Button Click Handling ---
        # The logic for deleting a poll has been removed as per user request.

        # --- Handle Vote Button Click ---
        if action_id.startswith("vote_option_"):  # This catches all vote buttons
            selected_choice = data["actions"][0]["value"]  # The choice they selected (actual choice text)

            # Find the poll in the database using message_ts and channel
            poll = polls.find_one({"message_ts": message_ts, "channel": channel})

            if poll:
                allow_multiple = poll.get("allow_multiple_votes", False)  # Reverted field name
                current_user_votes_from_db = poll.get("votes", {}).get(user_id,
                                                                       None)  # Get current vote, default to None

                if allow_multiple:
                    # In multiple vote mode, current_user_votes_from_db is expected to be a list
                    # Ensure it's a list even if it was unset or never set
                    current_user_votes_list = current_user_votes_from_db if isinstance(current_user_votes_from_db,
                                                                                       list) else []

                    if selected_choice in current_user_votes_list:
                        # If already voted for this, remove it (unvote for this specific option)
                        polls.update_one(
                            {"_id": poll["_id"]},
                            {"$pull": {f"votes.{user_id}": selected_choice}}
                        )
                        print(f"Removed vote for {user_id} from {selected_choice} (multiple votes mode)")
                    else:
                        # Add this choice to the user's array of votes
                        polls.update_one(
                            {"_id": poll["_id"]},
                            {"$addToSet": {f"votes.{user_id}": selected_choice}}  # $addToSet prevents duplicates
                        )
                        print(f"Added vote for {user_id} to {selected_choice} (multiple votes mode)")
                else:  # Single vote mode
                    # current_user_votes_from_db is a string (the chosen option) or None
                    if current_user_votes_from_db == selected_choice:
                        # User clicked the same vote button again in single-vote mode, so remove their vote
                        polls.update_one(
                            {"_id": poll["_id"]},
                            {"$unset": {f"votes.{user_id}": ""}}  # $unset removes the field
                        )
                        print(f"Removed vote for {user_id} on {selected_choice} (single mode)")
                    else:
                        # User voted for the first time or changed their vote in single-vote mode
                        # Replace any existing votes with the new one
                        polls.update_one(
                            {"_id": poll["_id"]},
                            {"$set": {f"votes.{user_id}": selected_choice}}  # Store as a string
                        )
                        print(f"Set/Updated vote for {user_id} to {selected_choice} (single mode)")

                # Get the updated votes for displaying the poll results
                # Fetch the poll again to get the latest state including the updated vote
                poll = polls.find_one({"message_ts": message_ts, "channel": channel})

                all_votes_flat = {}  # To count total votes per choice
                unique_voters = set()  # To count total unique respondents correctly
                total_individual_votes_cast = 0  # To count total individual votes for percentage base

                for uid, user_vote_data in poll.get("votes", {}).items():
                    if user_vote_data:  # Only count if the user actually has a vote (not unset/empty)
                        unique_voters.add(uid)  # Add user to unique voters set

                        if isinstance(user_vote_data, list):  # Multiple votes scenario
                            for choice_item in user_vote_data:
                                all_votes_flat.setdefault(choice_item, []).append(uid)
                                total_individual_votes_cast += 1  # Increment for each individual vote
                        else:  # Single vote scenario (user_vote_data is a string)
                            all_votes_flat.setdefault(user_vote_data, []).append(uid)
                            total_individual_votes_cast += 1  # Increment for single vote

                total_respondents = len(unique_voters)  # Correctly define total_respondents here
                display_total_votes_count = total_individual_votes_cast

                choices = poll["choices"]
                question = poll["question"]
                creator_id = poll.get("creator_id", "unknown")

                emoji_list = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

                # Reconstruct the Slack blocks to update the message
                # Start with the question
                blocks = [
                    {"type": "section", "text": {"type": "mrkdwn", "text": f"*{question}*"}}
                ]

                # Add "You may vote for multiple options" notice if allow_multiple_votes is True
                if allow_multiple:  # Use the 'allow_multiple' variable derived from poll settings
                    blocks.append({
                        "type": "context",
                        "elements": [
                            {"type": "mrkdwn", "text": "💡 _You may vote for multiple options_"}
                        ]
                    })

                # Add the updated results for each choice, including the button
                for i, choice in enumerate(choices):
                    current_emoji = emoji_list[i] if i < len(emoji_list) else "🔘"
                    user_ids_for_this_choice = all_votes_flat.get(choice, [])
                    vote_count = len(user_ids_for_this_choice)

                    # Calculate percentage for display
                    # Use total_individual_votes_cast for percentage base if multiple votes are allowed
                    if allow_multiple:
                        percentage_base = total_individual_votes_cast
                    else:  # Single vote mode
                        percentage_base = total_respondents  # total unique users

                    percentage = (vote_count / percentage_base * 100) if percentage_base > 0 else 0

                    # Format the mention text with spaces instead of newlines
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
                            "text": {"type": "plain_text", "text": current_emoji},  # Button with emoji
                            "value": choice,
                            "action_id": f"vote_option_{i}"
                        }
                    })

                # Add context information at the bottom (Total votes and Created by)
                context_elements_bottom = [
                    {"type": "mrkdwn", "text": f"*Total votes:* {display_total_votes_count}"},
                    # Changed label and used new variable
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

                # Update the Slack message with the new vote count
                async with httpx.AsyncClient() as client:
                    update_response = await client.post("https://slack.com/api/chat.update", headers=headers, json={
                        "channel": channel,
                        "ts": message_ts,
                        "blocks": blocks,
                        "text": question  # Fallback text for notifications
                    })
                print(f"Slack chat.update response status: {update_response.status_code}")
                print(f"Slack chat.update response text: {update_response.text}")

            return Response(status_code=200)  # Ensure a 200 OK is always returned for handled actions

    print(f"--- Unhandled Interaction Type: {data['type']} ---")
    return Response(status_code=200)  # Always return 200 to Slack for unhandled actions
