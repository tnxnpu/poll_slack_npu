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
            "private_metadata": channel_id,
            "title": {"type": "plain_text", "text": "Create a Poll"},
            "submit": {"type": "plain_text", "text": "Create"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "question_block",
                    "label": {"type": "plain_text", "text": "Poll Question"},
                    "element": {"type": "plain_text_input", "action_id": "question_input", "placeholder": {"type": "plain_text", "text": "What do you want to ask?"}}
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
    }


    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient() as client:
        await client.post("https://slack.com/api/views.open", headers=headers, json=modal)

    return PlainTextResponse("OK") # Slack expects a 200 OK response quickly
