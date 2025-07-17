# main.py

import httpx
import json
from fastapi import FastAPI, Request, Response
from pymongo import DESCENDING

# Import settings and database connection
from settings import SLACK_BOT_TOKEN
from interactions.poll_helpers import EMOJI_LIST
# Import both polls and drafts collections
from db import polls, drafts

# Import the refactored router from the interactions package
from interactions import interactions_router
from view_loader import get_create_poll_modal

app = FastAPI(
    title="Slack Poll App",
    description="A FastAPI server to handle Slack interactions for a poll application.",
    version="1.0.0",
)

# Include the router that handles all /slack/interactions callbacks
app.include_router(interactions_router)


@app.api_route("/healthz", methods=["GET", "HEAD"], tags=["System"])
def health_check():
    """A simple endpoint for Render's health check."""
    return {"status": "ok"}


@app.post("/slack/commands", tags=["Slack Commands"])
async def open_poll_modal_from_command(request: Request):
    """
    Handles the /poll slash command from Slack to open the poll creation modal.
    """
    try:
        form = await request.form()
        trigger_id = form.get("trigger_id")
        channel_id = form.get("channel_id")
        user_id = form.get("user_id")  # Get the user's ID

        if not trigger_id:
            return Response(status_code=400, content="trigger_id is required.")

        # --- Draft Loading Logic ---
        draft_state = None
        if user_id:
            draft_doc = drafts.find_one({"user_id": user_id})
            if draft_doc:
                draft_state = draft_doc.get("state")
        # --- End of Draft Logic ---

        # Load the modal view, potentially with draft data
        modal_view = get_create_poll_modal(trigger_id, channel_id, draft_state)

        headers = {
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            "Content-Type": "application/json; charset=utf-8"
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://slack.com/api/views.open",
                headers=headers,
                json=modal_view
            )
            response.raise_for_status()

        # Acknowledge the command immediately as required by Slack
        return Response(status_code=200)

    except Exception as e:
        print(f"Error handling slash command: {e}")
        return Response(status_code=500)


@app.post("/slack/events", tags=["Slack Events"])
async def handle_slack_events(request: Request):
    """
    Handles events from the Slack Events API, such as app_home_opened.
    """
    payload = await request.json()
    event_type = payload.get("type")

    # Slack's one-time challenge to verify the endpoint URL
    if event_type == "url_verification":
        return Response(content=payload.get("challenge"))

    # Handle the app_home_opened event to display the user's home tab
    event = payload.get("event", {})
    if event.get("type") == "app_home_opened":
        user_id = event.get("user")
        if user_id:
            await _publish_app_home(user_id)

    return Response(status_code=200)


async def _publish_app_home(user_id: str):
    """
    Constructs and publishes the App Home view for a given user.
    """
    try:
        # Base view structure from JSON file
        with open("views/app_home_view.json") as f:
            home_view = json.load(f)

        # Fetch the 5 most recent polls for the user
        recent_polls = list(polls.find({"creator_id": user_id}).sort("_id", DESCENDING).limit(5))

        if recent_polls:
            poll_blocks = [
                {"type": "divider"},
                {"type": "header", "text": {"type": "plain_text", "text": "Your Recent Polls"}}
            ]

            for poll in recent_polls:
                question = poll.get('question', 'Untitled Poll')
                poll_id_str = str(poll.get('_id'))
                messages = poll.get("messages", [])
                permalink = messages[0].get("permalink") if messages and messages[0].get("permalink") else "#"

                display_text = f"<{permalink}|*{question.replace('<!channel>', '').strip()}*>"
                if "<!channel>" in question:
                    display_text += " <!channel>"

                poll_blocks.extend([
                    {"type": "divider"},
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": display_text},
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
                    emoji = EMOJI_LIST[i] if i < len(EMOJI_LIST) else "🔘"
                    choice_text = choice.get("text", "N/A")
                    poll_blocks.append({
                        "type": "context",
                        "elements": [{"type": "mrkdwn", "text": f"{emoji} {choice_text}"}]
                    })

            home_view["blocks"].extend(poll_blocks)

        headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
        async with httpx.AsyncClient() as client:
            await client.post(
                "https://slack.com/api/views.publish",
                headers=headers,
                json={"user_id": user_id, "view": home_view},
            )
    except Exception as e:
        print(f"Error publishing App Home view for user {user_id}: {e}")
