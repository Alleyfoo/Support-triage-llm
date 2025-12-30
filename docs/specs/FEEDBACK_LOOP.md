## Closed-Loop Feedback (Milestone F)

### 7. Implementation Details & Constraints

#### 7.1 Configuration
* Use `IMAP_FOLDER_DRAFTS` (default: "Drafts") and `IMAP_FOLDER_SENT` (default: "Sent").
* If folder selection fails, list available folders to stdout to assist debugging.

#### 7.2 Content Parsing
* **Format:** The system operates on **Plain Text**.
* **Extraction:** When reading Sent items, prefer `text/plain` parts. If only `text/html` is found, convert to text using `app.email_preprocess`.
* **Cleanup:** Remove the `Internal Ref:` footer line before calculating Edit Distance.
* **Size Cap:** Truncate bodies at 100,000 characters to avoid bloat/DoS.

#### 7.3 Metrics
* **Edit Distance:** Store as `REAL`.
* Formula: `1.0 - difflib.SequenceMatcher(None, draft_body, sent_body).ratio()`
* Interpretation: `0.0 = Identical`, `1.0 = Complete Rewrite`.
