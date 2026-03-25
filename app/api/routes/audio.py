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

# Whisper model state — loaded once per worker process.
_whisper_model = None
_whisper_loading = False   # prevents concurrent load attempts


def _try_lower_oom_score() -> None:
    """Ask the Linux OOM killer to spare this process during model loading.

    Writing a negative value to /proc/self/oom_score_adj makes the kernel
    less likely to choose this process when it needs to free memory.
    Requires CAP_SYS_RESOURCE (root in most Docker containers).
    Fails silently when running unprivileged.
    """
    try:
        with open("/proc/self/oom_score_adj", "w") as f:
            f.write("-500")
        log.info("OOM score set to -500 — process is now deprioritised for OOM kills.")
    except OSError:
        log.debug("Could not lower OOM score (non-root or non-Linux). Proceeding without protection.")


def _check_available_memory_mb(required_mb: int = 300) -> int:
    """Return available RAM in MB, or -1 if psutil is not installed."""
    try:
        import psutil
        return psutil.virtual_memory().available // (1024 * 1024)
    except ImportError:
        return -1


def _ensure_whisper_loaded() -> None:
    """Load the faster-whisper model into _whisper_model if not already loaded.

    Must be called from a worker thread (via asyncio.to_thread) — never from
    the async event-loop thread — because WhisperModel() performs heavy I/O
    and CTranslate2 initialisation.

    Also applies HF_TOKEN (if configured) so that model downloads use
    authenticated HuggingFace requests: higher rate limits, no throttling.
    """
    global _whisper_model
    if _whisper_model is not None:
        return  # already loaded

    # Lower OOM priority before allocating ~150 MB for the model.
    _try_lower_oom_score()

    # Warn early if memory looks tight (base model needs ~150 MB at runtime,
    # up to ~300 MB transiently during loading due to double-buffering).
    available = _check_available_memory_mb(required_mb=300)
    if available != -1:
        if available < 150:
            log.error(
                f"Only {available} MB available — Whisper base model may not load. "
                "Consider switching to the 'tiny' model or adding swap space."
            )
        elif available < 300:
            log.warning(
                f"Only {available} MB available — model load may trigger OOM. "
                "Proceeding; add swap or upgrade instance if this crashes."
            )
        else:
            log.info(f"Memory check: {available} MB available — sufficient for model load.")

    from faster_whisper import WhisperModel
    from app.core.config import settings

    # Apply HF token before any HuggingFace network call.
    if settings.HF_TOKEN:
        try:
            import huggingface_hub
            huggingface_hub.login(token=settings.HF_TOKEN, add_to_git_credential=False)
            log.info("HuggingFace token applied — authenticated downloads enabled.")
        except Exception as e:
            log.warning(f"HF_TOKEN login failed (non-fatal, continuing anonymously): {e}")

    log.info("Loading faster-whisper 'base' model on CPU…")
    _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
    log.info("faster-whisper model ready.")


def _load_and_transcribe(tmp_path: str) -> tuple[str, str]:
    """Ensure model is loaded then fully transcribe the audio file.

    Must be called via asyncio.to_thread so that:
    - Model construction never blocks the event-loop thread.
    - The CTranslate2 segments *generator* is consumed here, inside the
      thread — iterating it on the event loop was the root cause of the
      worker crashes seen in production.
    """
    _ensure_whisper_loaded()
    segments_gen, info = _whisper_model.transcribe(tmp_path, beam_size=5)
    # Consume the generator fully HERE, inside the thread.
    text = " ".join(seg.text.strip() for seg in segments_gen).strip()
    return text, info.language


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/warmup")
async def warmup_model():
    """Trigger background Whisper model pre-warm.

    The frontend calls this once the interview session page is fully loaded
    so the model is ready before the user speaks. Returns immediately —
    loading happens in a background thread and does not block the response.
    """
    global _whisper_loading

    if _whisper_model is not None:
        return {"status": "ready"}

    if _whisper_loading:
        return {"status": "warming"}

    # Start background load — only one concurrent attempt allowed.
    _whisper_loading = True

    async def _background_load():
        global _whisper_loading
        try:
            await asyncio.to_thread(_ensure_whisper_loaded)
            log.info("Whisper warmup complete (triggered by /audio/warmup).")
        except Exception as exc:
            log.error(f"Whisper warmup failed: {exc}")
        finally:
            _whisper_loading = False

    asyncio.create_task(_background_load())
    return {"status": "warming"}


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
