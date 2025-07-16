# main.py

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response
import httpx
from interactions import interactions_router
from settings import SLACK_BOT_TOKEN

import json
from db import polls
from pymongo import DESCENDING

app = FastAPI()
app.include_router(interactions_router)


@app.api_route("/healthz", methods=["GET", "HEAD"])
def health_check():
    """A simple endpoint for Render's health check."""
    return {"status": "ok"}


@app.post("/slack/commands")
async def open_poll_modal(request: Request):
    """
    Handles the Slack slash command to open the new dynamic poll creation modal.
    """
    form = await request.form()
    trigger_id = form.get("trigger_id")
    channel_id = form.get("channel_id")

    # This is the new, dynamic modal structure
    modal = {
        "trigger_id": trigger_id,
        "view": {
            "type": "modal",
            "callback_id": "submit_poll_modal",
            "private_metadata": "",  # No longer used for channel, can be used for other things
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
                    "element": {"type": "plain_text_input", "action_id": "choice_input_0",
                                "placeholder": {"type": "plain_text", "text": "Write something"}}
                },
                {
                    "type": "input",
                    "block_id": "choice_block_1",
                    "optional": True,
                    "label": {"type": "plain_text", "text": "Option 2 (optional)"},
                    "element": {"type": "plain_text_input", "action_id": "choice_input_1",
                                "placeholder": {"type": "plain_text", "text": "Write something"}}
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
                        "action_id": "settings_checkboxes",
                        "options": [
                            {
                                "text": {"type": "plain_text", "text": "Allow multiple votes"},
                                "value": "allow_multiple"
                            },
                            {
                                "text": {"type": "plain_text", "text": "Allow others to add options"},
                                "value": "allow_others_to_add"
                            }
                        ]
                    }
                },
                {
                    "type": "input",
                    "block_id": "channel_block",
                    "label": {"type": "plain_text", "text": "Select channel(s) to post"},
                    "element": {
                        "type": "multi_conversations_select",
                        "action_id": "channels_input",
                        "initial_conversations": [channel_id] if channel_id else [],
                        "placeholder": {"type": "plain_text", "text": "Select channels..."}
                    }
                }
            ]
        }
    }

    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient() as client:
        await client.post("https://slack.com/api/views.open", headers=headers, json=modal)

    return Response(status_code=200)  # Slack expects a 200 OK response quickly


@app.post("/slack/events")
async def handle_slack_events(request: Request):
    """
    Handles events from the Slack Events API, including app_home_opened.
    """
    payload = await request.json()
    event_type = payload.get("type")

    # Slack's one-time challenge to verify the URL
    if event_type == "url_verification":
        return Response(content=payload.get("challenge"))

    # Handle the app_home_opened event
    event = payload.get("event", {})
    if event.get("type") == "app_home_opened":
        user_id = event.get("user")
        if user_id:
            try:
                # Base view structure
                with open("views/app_home_modal.json") as f:
                    home_view = json.load(f)

                # Fetch recent polls for the user
                recent_polls = list(polls.find({"creator_id": user_id}).sort("_id", DESCENDING).limit(5))

                if recent_polls:
                    poll_blocks = [
                        {"type": "divider"},
                        {"type": "header", "text": {"type": "plain_text", "text": "Your Recent Polls"}}
                    ]
                    emoji_list = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

                    for poll in recent_polls:
                        question = poll.get('question', 'Untitled Poll')

                        # Get the permalink from the first message, if it exists
                        messages = poll.get("messages", [])
                        permalink = messages[0].get("permalink") if messages and messages[0].get("permalink") else "#"

                        poll_blocks.append({"type": "divider"})

                        # Create a section with a clickable mrkdwn link for the question
                        poll_blocks.append({
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"<{permalink}|*{question}*>"
                            }
                        })

                        # Add the list of choices below the question
                        choices = poll.get("choices", [])
                        for i, choice in enumerate(choices):
                            emoji = emoji_list[i] if i < len(emoji_list) else "🔘"
                            choice_text = choice.get("text", "N/A")
                            poll_blocks.append({
                                "type": "context",
                                "elements": [
                                    {"type": "mrkdwn", "text": f"{emoji} {choice_text}"}
                                ]
                            })

                    home_view["blocks"].extend(poll_blocks)

                async with httpx.AsyncClient() as client:
                    await client.post(
                        "https://slack.com/api/views.publish",
                        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
                        json={"user_id": user_id, "view": home_view},
                    )
            except (FileNotFoundError, json.JSONDecodeError) as e:
                print(f"Error processing app_home_opened event: {e}")

    return Response(status_code=200)