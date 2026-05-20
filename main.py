import os
import time
from contextlib import asynccontextmanager

import numpy as np
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pywhispercpp.model import Model

MODEL_PATH = os.environ.get("WHISPER_MODEL", "/models/nb-whisper-medium-q5_0.bin")
HF_MODELS_PATH = os.environ.get("HF_MODELS", "/models")
WHISPER_SAMPLE_RATE = 16000
MAX_DURATION_S = 120
MIN_DURATION_S = 0.5

whisper_model: Model | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global whisper_model
    print(f"Laster Whisper-modell fra {MODEL_PATH}...")
    t0 = time.time()
    whisper_model = Model(MODEL_PATH, n_threads=4, language="no")
    print(f"Modell lastet på {time.time() - t0:.1f}s")
    yield
    whisper_model = None


app = FastAPI(title="Skansen – Norsk tale-til-tekst", lifespan=lifespan)


@app.post("/api/transcribe")
async def transcribe(
    audio: UploadFile = File(...),
    sample_rate: int = Form(48000),
):
    raw = await audio.read()
    if len(raw) == 0:
        raise HTTPException(400, "Tom lydfil")

    samples = np.frombuffer(raw, dtype=np.float32)
    duration = len(samples) / sample_rate

    if duration < MIN_DURATION_S:
        return JSONResponse({"text": "", "duration": duration})
    if duration > MAX_DURATION_S:
        raise HTTPException(400, f"Klippet er for langt ({duration:.0f}s, maks {MAX_DURATION_S}s)")

    if sample_rate != WHISPER_SAMPLE_RATE:
        ratio = WHISPER_SAMPLE_RATE / sample_rate
        new_length = int(len(samples) * ratio)
        indices = np.arange(new_length) / ratio
        left = np.floor(indices).astype(int)
        right = np.minimum(left + 1, len(samples) - 1)
        frac = (indices - left).astype(np.float32)
        samples = samples[left] * (1 - frac) + samples[right] * frac

    segments = whisper_model.transcribe(samples)
    text = " ".join(seg.text.strip() for seg in segments if seg.text.strip())

    return JSONResponse({"text": text, "duration": duration})


@app.get("/api/health")
async def health():
    return {"status": "ok", "model_loaded": whisper_model is not None}


@app.get("/pcm-worklet.js")
async def pcm_worklet():
    return FileResponse("static/pcm-worklet.js", media_type="application/javascript")


@app.get("/whisper-worker.js")
async def whisper_worker():
    return FileResponse("static/whisper-worker.js", media_type="application/javascript")


app.mount("/hf", StaticFiles(directory=HF_MODELS_PATH), name="hf")
app.mount("/", StaticFiles(directory="static", html=True), name="static")
