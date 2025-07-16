# interactions/poll_helpers.py

import httpx
from bson.objectid import ObjectId
from db import polls
from settings import SLACK_BOT_TOKEN

EMOJI_LIST = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
async def update_all_poll_messages(poll_id: ObjectId, client: httpx.AsyncClient):
    """
    Fetches the latest poll state from the DB, rebuilds the Slack message blocks,
    and updates all previously sent messages for that poll.
    """
    poll = polls.find_one({"_id": poll_id})
    if not poll:
        print(f"Cannot update messages for poll {poll_id}: Poll not found.")
        return

    # --- Calculate Vote Counts ---
    total_individual_votes_cast = sum(len(choice.get("voters", [])) for choice in poll.get("choices", []))
    unique_voters = {voter for choice in poll.get("choices", []) for voter in choice.get("voters", [])}
    total_respondents = len(unique_voters)

    # --- Build Slack Message Blocks ---
    blocks = build_poll_blocks(poll, total_individual_votes_cast, total_respondents)
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}

    for msg_info in poll.get("messages", []):
        if msg_info.get("channel") and msg_info.get("ts"):
            await client.post(
                "https://slack.com/api/chat.update",
                headers=headers,
                json={"channel": msg_info["channel"], "ts": msg_info["ts"], "blocks": blocks, "text": poll["question"]}
            )


def build_poll_blocks(poll: dict, total_votes: int = 0, total_respondents: int = 0) -> list:
    """A centralized function to build the Slack blocks for a poll."""
    question = poll["question"]
    choices = poll.get("choices", [])
    creator_id = poll.get("creator_id", "unknown")
    allow_multiple = poll.get("allow_multiple_votes", False)
    allow_add = poll.get("allow_others_to_add_options", False)

    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{question}*"},
         "accessory": {"type": "overflow", "options": [{"text": {"type": "plain_text", "text": "Settings"},
                                                        "value": f"settings_{poll['_id']}"}],
                       "action_id": "open_poll_settings"}}
    ]
    if allow_multiple:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "💡 _Multiple votes are allowed_"}]})

    for i, choice in enumerate(choices):
        voters = choice.get("voters", [])
        vote_count = len(voters)
        percentage_base = total_votes if allow_multiple else total_respondents
        percentage = (vote_count / percentage_base * 100) if percentage_base > 0 else 0
        mention_text = " ".join(f"<@{uid}>" for uid in voters) if voters else ""
        emoji = EMOJI_LIST[i] if i < len(EMOJI_LIST) else "🔘"

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"{emoji} *{choice['text']}* `{vote_count}` {percentage:.0f}% \n{mention_text}"},
            "accessory": {"type": "button", "text": {"type": "plain_text", "text": emoji},
                          "value": str(choice["_id"]), "action_id": "vote_for_choice"}
        })

    if allow_add:
        blocks.append(
            {"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "Add Option"},
                                              "value": str(poll["_id"]), "action_id": "open_add_option_modal"}]})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"*Total votes:* {total_votes}"},
                                                   {"type": "mrkdwn", "text": f"Created by <@{creator_id}>"}]})
    return blocks
