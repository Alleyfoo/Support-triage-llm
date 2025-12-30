# Support Triage Copilot — Yhteenveto (FI)

Paikallisesti ajettava triage- ja luonnostyökalu, joka käyttää Ollamaa sekä SQLite/IMAP:ia. Se lukee viestit jonoon, tekee triagen LLM:llä, ehdottaa työkaluja todisteiden keruuseen, luonnostelee vastaukset ja oppii ihmisen muutoksista (suljettu palautesilmukka).

Pääkohdat
- Päättöjen sähköpostityö: luonnokset ilmestyvät IMAP Luonnokset -kansioon; Lähetetyt-kansiota seurataan muutosten mittaamiseksi ja parantamiseksi.
- Dynaamiset työkalut: LLM ehdottaa rekisterin työkaluja; voit lisätä uusia työkaluja ilman ydinkoodin muutoksia.
- Few-shot/RAG: hakee aiempia “kultaisia” tapauksia promptiin, jotta sävy/rakenne osuu heti.
- Tietosuoja ensin: ei ulkoisia SaaS-palveluja; käyttää paikallista Ollama-päätettä ja paikallista tallennusta.

Ajo
- Docker: `docker compose up -d --build` (käyttää `.env`:iä; mounttaa `./data` ja `./docs`).
- Manuaalinen: `python tools/daemon.py` (vaatii käynnissä olevan Ollaman).

Tilannekuva
- `python tools/status.py` näyttää viimeisimmän ingest/triage/learning-ajan ja jonon koon.

Keskeiset asetukset
- `TRIAGE_MODE=llm`, `MODEL_NAME=llama3.1:8b`, `OLLAMA_EMBED_MODEL=nomic-embed-text`
- IMAP: `IMAP_HOST`, `IMAP_USERNAME`, `IMAP_PASSWORD`, `IMAP_FOLDER_DRAFTS`, `IMAP_FOLDER_SENT`
- Knowledge: `KNOWLEDGE_SOURCE=./data/knowledge.md` (oma konteksti avain/arvo -muodossa)

Tärkeät tiedostot
- `tools/daemon.py` — valvoja ingest/triage/luonnos-synkronointi/sent-palaute/oppiminen.
- `tools/status.py` — nopea tilannekatsaus.
- `tools/run_learning_cycle.py` — yöajon oppimissykli.
- `docs/specs/FEEDBACK_LOOP.md` — IMAP-pohjainen palautesilmukka.
- `docs/specs/DYNAMIC_FEW_SHOT.md` — few-shot/RAG triage -suunnitelma.
