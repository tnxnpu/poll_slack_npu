import os
from slack_bolt import App
from slack_bolt.adapter.fastapi import SlackRequestHandler
from fastapi import FastAPI, Request
from dotenv import load_dotenv

load_dotenv()

slack_app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    signing_secret=os.environ["SLACK_SIGNING_SECRET"],
)

# -------------------------------- Modal opener ----------------------------------
@slack_app.command("/clmd")
def open_modal(ack, body, client):
    ack()

    client.views_open(
        trigger_id=body["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "custom_close_modal",
            "title": {"type": "plain_text", "text": "Demo"},
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "*Press the button to close me*"},
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "action_id": "custom_close_button",
                            "style": "primary",
                            "text": {"type": "plain_text", "text": "Custom Close"},
                        }
                    ],
                },
            ],
        },
    )

# --------------------------- Handle the custom button ---------------------------
@slack_app.action("custom_close_button")
def close_via_button(ack, body, client):
    # 1. Ack within 3 s so Slack knows we heard the click
    ack()

    # 2. Replace the view with an otherwise-empty modal that has only a built-in Close
    client.views_update(
        view_id=body["container"]["view_id"],
        hash=body["view"]["hash"],
        view={
            "type": "modal",
            "title": {"type": "plain_text", "text": "Click `Close` to close"},
            "close": {"type": "plain_text", "text": "Close"},
            "blocks": [],
        },
    )
    # Slack auto-dismisses the modal right after updating

# ------------------------------- FastAPI glue -----------------------------------
app = FastAPI()
handler = SlackRequestHandler(slack_app)

@app.post("/slack/events")
async def endpoint(req: Request):
    return await handler.handle(req)
