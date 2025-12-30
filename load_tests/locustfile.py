import os
import uuid
from locust import HttpUser, task, between


API_KEY = os.environ.get("INGEST_API_KEY", "dev-api-key")


class ChatLoadUser(HttpUser):
    wait_time = between(0.5, 2.0)

    @task
    def enqueue_chat(self) -> None:
        conversation_id = str(uuid.uuid4())
        payload = {
            "conversation_id": conversation_id,
            "text": "Hello, can you tell me about your warranty?",
            "end_user_handle": f"loadtest-{conversation_id[:8]}",
            "channel": "web_chat",
        }
        headers = {"X-API-KEY": API_KEY}
        self.client.post("/chat/enqueue", json=payload, headers=headers)
