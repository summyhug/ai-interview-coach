# AI Interview Coach

Record interview answers, get transcribed and scored. Guided interview mode with voice questions, per-question feedback, and retry flow.

## Prerequisites

- Python 3.9+
- **ffmpeg** (for WebM conversion): `brew install ffmpeg`
- Microphone for recording
- **LLM** (choose one):
  - **Gemini** (faster, cloud): Set `GEMINI_API_KEY` or `GOOGLE_API_KEY`
  - **Ollama** (local, offline): [Ollama](https://ollama.com) installed and running

## Setup

1. **LLM** — Choose one:
   - **Gemini** (recommended for speed): Get an API key from [Google AI Studio](https://aistudio.google.com/). Create a `.env` file in the project root:
     ```bash
     GEMINI_API_KEY=your_key_here
     ```
     Or export: `export GEMINI_API_KEY=your_key_here`
   - **Ollama** (local): `ollama pull llama3.2` and run `ollama serve`

2. **Create a virtual environment and install dependencies**:

   ```bash
   cd backend
   python -m venv venv
   source venv/bin/activate   # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Run the server**:

   ```bash
   uvicorn main:app --reload --host 0.0.0.0 --port 8000
   ```

4. **Open in browser**: http://localhost:8000

## Usage

1. Click **Start Interview** to begin recording.
2. Speak your answers (pauses create natural segments).
3. Click **End Interview** when done.
4. Wait for transcription and scoring (first run downloads the Whisper model).
5. Review per-turn rubric scores, filler stats, and optional rewrite suggestions.

## Scoring Rubric

Each answer turn is evaluated on:

- Direct answer in first ~10 seconds
- Specific example
- Quantified impact
- Tradeoffs mentioned
- Crisp takeaway at end
- Filler words ("um", "like"), long pauses, trailing sentences
- Question type: Behavioral | Product sense | Technical | Estimation

## Configuration

- `GEMINI_API_KEY` or `GOOGLE_API_KEY`: Use Gemini (cloud) for faster scoring. Put in `.env` (gitignored) or export as env var.
- `OLLAMA_MODEL`: Ollama model name when using local LLM (default: `llama3.2`)
- `LLM_PROVIDER`: `auto` (try Gemini first) | `gemini` | `ollama`
- **Voice**: Edge TTS (Microsoft neural voices) used by default; falls back to browser TTS if unavailable

## Project Structure

```
ai-interview-coach/
├── backend/
│   ├── main.py          # FastAPI app
│   ├── transcribe.py    # faster-whisper
│   ├── scorer.py        # Ollama prompts
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   └── app.js
└── README.md
```
