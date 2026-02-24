# AI Interview Coach

Record interview answers, get transcribed and scored by a local LLM. Offline-capable using faster-whisper and Ollama.

## Prerequisites

- Python 3.9+
- [Ollama](https://ollama.com) installed and running
- **ffmpeg** (for WebM conversion): `brew install ffmpeg`
- Microphone for recording

## Setup

1. **Pull an Ollama model** (if not already done):

   ```bash
   ollama pull llama3.2
   ```

   Or use `mistral` or another model; set `OLLAMA_MODEL` if different.

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

- `OLLAMA_MODEL`: Ollama model name (default: `llama3.2`)
- Whisper model: `base` (in `transcribe.py`) — use `small` for better accuracy, more RAM

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
