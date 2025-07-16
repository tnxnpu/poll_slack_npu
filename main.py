# main.py

from fastapi import FastAPI, Request
from fastapi.responses import Response
import httpx
from interactions import interactions_router
from settings import SLACK_BOT_TOKEN
# Import the new helper function
from view_loader import get_create_poll_modal

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

    # Load the modal view from the JSON file using the helper
    modal = get_create_poll_modal(trigger_id, channel_id)

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
    Handles events from the Slack Events API.
    Acts as a router for different event types.
    """
    payload = await request.json()
    event_type = payload.get("type")

    # Handle Slack's URL verification challenge
    if event_type == "url_verification":
        return Response(content=payload.get("challenge"))

    event = payload.get("event", {})
    # Route app_home_opened events to the dedicated handler
    if event.get("type") == "app_home_opened":
        user_id = event.get("user")
        if user_id:
            await _publish_app_home(user_id)

    return Response(status_code=200)


async def _publish_app_home(user_id: str):
    """
    Constructs and publishes the App Home view for a given user,
    including a dynamic list of their recent polls.
    """
    try:
        # Base view structure from JSON file
        with open("views/app_home_view.json") as f:
            home_view = json.load(f)

        # Fetch the 5 most recent polls for the user
        recent_polls = list(polls.find({"creator_id": user_id}).sort("_id", DESCENDING).limit(5))

        # If polls are found, build and add the poll blocks
        if recent_polls:
            poll_blocks = [
                {"type": "divider"},
                {"type": "header", "text": {"type": "plain_text", "text": "Your Recent Polls"}}
            ]
            emoji_list = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

            for poll in recent_polls:
                question = poll.get('question', 'Untitled Poll')
                poll_id_str = str(poll.get('_id'))
                messages = poll.get("messages", [])
                permalink = messages[0].get("permalink") if messages and messages[0].get("permalink") else "#"

                poll_blocks.extend([
                    {"type": "divider"},
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"<{permalink}|*{question}*>"},
                        "accessory": {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Quick View", "emoji": True},
                            "action_id": "view_poll_details",
                            "value": poll_id_str
                        }
                    }
                ])

                choices = poll.get("choices", [])
                for i, choice in enumerate(choices):
                    emoji = emoji_list[i] if i < len(emoji_list) else "🔘"
                    choice_text = choice.get("text", "N/A")
                    poll_blocks.append({
                        "type": "context",
                        "elements": [{"type": "mrkdwn", "text": f"{emoji} {choice_text}"}]
                    })

            home_view["blocks"].extend(poll_blocks)

        # Publish the view to the user's App Home
        async with httpx.AsyncClient() as client:
            await client.post(
                "https://slack.com/api/views.publish",
                headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
                json={"user_id": user_id, "view": home_view},
            )
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Error publishing App Home view: {e}")


