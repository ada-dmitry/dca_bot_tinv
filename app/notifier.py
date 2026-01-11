import logging
from typing import Optional

import requests


class Notifier:
    def __init__(self, token: str):
        self.token = token
        self.api_url = f"https://api.telegram.org/bot{token}"
        self.logger = logging.getLogger(__name__)

    def send_message(self, user_id: int, text: str) -> Optional[dict]:
        try:
            url = f"{self.api_url}/sendMessage"
            payload = {
                "chat_id": user_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            self.logger.error(f"Failed to send message to {user_id}: {e}")
            return None
