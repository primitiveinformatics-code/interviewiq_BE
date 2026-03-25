from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
import asyncio
import tempfile
import os
import io
from app.core.logging_config import get_logger

router = APIRouter()
log = get_logger("api.audio")

# Whisper model — loaded lazily on the first STT call, inside a thread so it
# never blocks the async event loop.
_whisper_model = None


def _load_and_transcribe(tmp_path: str) -> tuple[str, str]:
    """Load (or reuse) the Whisper model and fully transcribe the file.

    This function must be called via asyncio.to_thread so that:
    - Model construction (heavy I/O + CTranslate2 init) never runs on the
      event-loop thread.
    - The segments *generator* is consumed here, inside the thread, so
      CTranslate2 inference never executes on the event-loop thread.
      (Iterating a CTranslate2 generator on the event loop was the root cause
      of the worker crashes / OOM kills seen in production.)
    """
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        log.info("Loading faster-whisper 'base' model on CPU (first-time, may take ~30s)...")
        _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
        log.info("faster-whisper model ready.")

    segments_gen, info = _whisper_model.transcribe(tmp_path, beam_size=5)
    # Consume the generator fully HERE, inside the thread.
    text = " ".join(seg.text.strip() for seg in segments_gen).strip()
    return text, info.language


class TTSRequest(BaseModel):
    text: str
    voice: str = "en-US-AriaNeural"


@router.post("/tts")
async def text_to_speech(body: TTSRequest):
    """Convert text to speech using edge-tts. Returns MP3 audio bytes."""
    import edge_tts
    try:
        audio_buf = io.BytesIO()
        communicate = edge_tts.Communicate(body.text, body.voice)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_buf.write(chunk["data"])
        audio_bytes = audio_buf.getvalue()
        if not audio_bytes:
            raise HTTPException(status_code=500, detail="TTS produced no audio")
        log.info(f"TTS: generated {len(audio_bytes)} bytes for {len(body.text)}-char text")
        return Response(content=audio_bytes, media_type="audio/mpeg")
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"TTS error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stt")
async def speech_to_text(audio: UploadFile = File(...)):
    """Transcribe uploaded audio to text using faster-whisper."""
    try:
        audio_bytes = await audio.read()
        filename = audio.filename or "audio.wav"
        ext = os.path.splitext(filename)[1] or ".wav"

        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            # _load_and_transcribe loads the model (if needed) AND consumes
            # the CTranslate2 generator — all inside a worker thread.
            text, language = await asyncio.to_thread(_load_and_transcribe, tmp_path)
            log.info(f"STT: transcribed {len(audio_bytes)} bytes → '{text[:80]}'")
            return {"text": text, "language": language}
        finally:
            os.unlink(tmp_path)

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"STT error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
