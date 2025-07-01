from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response
import httpx
from interactions import interactions_router
from settings import SLACK_BOT_TOKEN

import json
from db import polls


app = FastAPI()
app.include_router(interactions_router)

@app.post("/slack/commands")
async def open_poll_modal(request: Request):
    """
    Handles the Slack slash command to open the poll creation modal.
    """
    form = await request.form()
    trigger_id = form.get("trigger_id")
    channel_id = form.get("channel_id")

    modal = {
        "trigger_id": trigger_id,
        "view": {
            "type": "modal",
            "callback_id": "submit_poll_modal", # Callback ID for modal submission
            "private_metadata": channel_id, # Pass channel ID to the modal submission handler
            "title": {"type": "plain_text", "text": "Create Poll"},
            "submit": {"type": "plain_text", "text": "Submit"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "question_block",
                    "label": {"type": "plain_text", "text": "Poll Question"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "question_input" # Action ID for question input
                    }
                },
                {
                    "type": "input",
                    "block_id": "choices_block",
                    "label": {"type": "plain_text", "text": "Choices (one per line)"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "choices_input", # Action ID for choices input
                        "multiline": True
                    }
                },
                {
                    "type": "input",
                    "block_id": "multiple_votes_block", # Reverted block_id
                    "element": {
                        "type": "checkboxes",
                        "options": [
                            {
                                "text": {
                                    "type": "plain_text",
                                    "text": "Allow multiple votes" # Reverted text
                                },
                                "value": "allow_multiple"
                            }
                        ],
                        "action_id": "allow_multiple_votes_checkbox" # Reverted action_id
                    },
                    "label": {"type": "plain_text", "text": "Settings"},
                    "optional": True # Make it optional so poll can be created without checking
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

    return PlainTextResponse("OK") # Slack expects a 200 OK response quickly
