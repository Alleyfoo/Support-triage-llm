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
            "text": "Emails bouncing to contoso.com",
            "tenant": f"loadtest-{conversation_id[:8]}",
            "source": "locust",
        }
        headers = {"X-API-KEY": API_KEY}
        self.client.post("/triage/enqueue", json=payload, headers=headers)
