import json
from pathlib import Path

import pandas as pd
import streamlit as st

from app.chat_service import ChatService
from tools import chat_dispatcher, chat_ingest, chat_worker

QUEUE_PATH = Path("data/email_queue.xlsx")
TRANSCRIPT_PATH = Path("data/chat_web_transcript.jsonl")


st.set_page_config(page_title="CS Chatbot LLM", layout="wide")
st.title("CS Chatbot LLM Playground")
st.caption(
    "Queue-driven chatbot demo. Use the controls below to enqueue customer messages, run the worker, "
    "dispatch replies, and review the transcript."
)


def _ensure_dataframe() -> pd.DataFrame:
    if QUEUE_PATH.exists():
        df = pd.read_excel(QUEUE_PATH)
        return chat_worker.ensure_chat_columns(df)
    return chat_worker.ensure_chat_columns(pd.DataFrame())


def _load_transcript() -> list[dict]:
    if not TRANSCRIPT_PATH.exists():
        return []
    entries: list[dict] = []
    for line in TRANSCRIPT_PATH.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            entries.append(json.loads(text))
        except json.JSONDecodeError:
            continue
    return entries


def _render_transcript(entries: list[dict]) -> None:
    if not entries:
        st.info(
            "Transcript is empty. Enqueue a message, run the worker, then dispatch to record a reply."
        )
        return
    for entry in entries[-20:]:
        payload = entry.get("response", {})
        content = payload.get("content") or "[empty response]"
        st.markdown(
            f"**Bot » {entry.get('conversation_id', 'unknown')}** — {content}"
        )


with st.sidebar:
    st.subheader("Demo Workflow")
    st.markdown(
        "1. Add a customer message via the form.\n"
        "2. Run the worker to generate a response.\n"
        "3. Run the dispatcher to log the reply to the transcript.\n"
        "4. Reload the transcript to preview the conversation."
    )
    st.markdown("Resources:")
    st.markdown("- `tools/chat_ingest.py` — CLI intake")
    st.markdown("- `tools/chat_worker.py` — queue worker")
    st.markdown("- `tools/chat_dispatcher.py` — transcript logger")


with st.form("enqueue_form", clear_on_submit=True):
    st.subheader("1. Enqueue a chat message")
    message = st.text_area("Message", placeholder="Hi! When were you founded?", height=120)
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        conversation_id = st.text_input("Conversation ID", value="demo-web")
    with col_b:
        end_user = st.text_input("End-user handle", value="demo-user")
    with col_c:
        channel = st.text_input("Channel", value="web_chat")
    submitted = st.form_submit_button("Add to queue")
    if submitted:
        payload = {
            "conversation_id": conversation_id,
            "text": message,
            "end_user_handle": end_user,
            "channel": channel,
        }
        count = chat_ingest.ingest_messages(QUEUE_PATH, [payload])
        if count:
            st.success(f"Enqueued {count} message(s) to {QUEUE_PATH}")
        else:
            st.warning("No text provided — nothing enqueued.")


col1, col2 = st.columns(2)
with col1:
    st.subheader("2. Process the queue")
    if st.button("Run chat worker once", use_container_width=True):
        processed = chat_worker.process_once(
            QUEUE_PATH,
            processor_id="streamlit-worker",
            chat_service=ChatService(),
        )
        if processed:
            st.success("Worker processed a queued message.")
        else:
            st.info("No queued messages found.")

with col2:
    st.subheader("3. Dispatch demo reply")
    if st.button("Dispatch via web demo", use_container_width=True):
        TRANSCRIPT_PATH.parent.mkdir(parents=True, exist_ok=True)
        dispatched = chat_dispatcher.dispatch_once(
            QUEUE_PATH,
            dispatcher_id="streamlit-dispatcher",
            adapter="web-demo",
            adapter_target=str(TRANSCRIPT_PATH),
        )
        if dispatched:
            st.success(f"Logged {dispatched} response(s) to {TRANSCRIPT_PATH}")
        else:
            st.info("Nothing waiting for dispatch.")


st.subheader("Current queue snapshot")
queue_df = _ensure_dataframe()
if queue_df.empty:
    st.write("Queue is empty.")
else:
    st.dataframe(
        queue_df.tail(20)[
            [
                "conversation_id",
                "payload",
                "status",
                "processor_id",
                "delivery_status",
                "matched",
                "missing",
            ]
        ],
        use_container_width=True,
    )


st.subheader("4. Transcript preview")
if st.button("Load latest transcript", key="load-transcript-sidebar"):
    st.experimental_rerun()
transcript_entries = _load_transcript()
_render_transcript(transcript_entries)
