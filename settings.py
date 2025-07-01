import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")