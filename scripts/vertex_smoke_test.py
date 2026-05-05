"""Smoke test for Vertex AI Gemini models (google-genai SDK).

This script intentionally does NOT rely on model listing APIs (which may vary by
SDK version and do not guarantee regional/permission availability). Instead, it
probes specific model IDs with minimal requests and prints clear pass/fail
errors.

Prereqs (local):
  - Create `credentials.json` at repo root (service account JSON, untracked)
  - Export env vars:
      PROJECT_ID=your-gcp-project
      VERTEX_LOCATION=us-central1   (or another supported region)
      GOOGLE_APPLICATION_CREDENTIALS=./credentials.json

Optional fixtures (recommended):
  - tests/fixtures/sample.mp3
  - tests/fixtures/sample.jpg
  - tests/fixtures/sample.mp4
If fixtures are missing, the corresponding probe is skipped.
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing env var: {name}")
    return value


def _maybe_load_bytes(path: Path) -> bytes | None:
    if not path.exists():
        return None
    return path.read_bytes()


def _print_header(title: str) -> None:
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)

def _split_models(value: str) -> list[str]:
    items = []
    for raw in value.split(","):
        m = raw.strip()
        if m:
            items.append(m)
    return items


def _try_models(client, models: list[str], *, contents, config=None, label: str) -> tuple[str, object]:
    last_exc: Exception | None = None
    for model in models:
        try:
            resp = client.models.generate_content(model=model, contents=contents, config=config)
            return model, resp
        except Exception as e:
            last_exc = e
            print(f"- FAIL {label}: model={model}: {e}")
    raise RuntimeError(
        f"All model candidates failed for {label}. "
        f"Try VERTEX_LOCATION=global and ensure your project has access. "
        f"Last error: {last_exc}"
    ) from last_exc


def main() -> int:
    project_id = _require_env("PROJECT_ID")
    location = _require_env("VERTEX_LOCATION")
    creds_path = _require_env("GOOGLE_APPLICATION_CREDENTIALS")

    creds_file = Path(creds_path)
    if not creds_file.exists():
        raise RuntimeError(
            f"GOOGLE_APPLICATION_CREDENTIALS points to missing file: {creds_path}"
        )

    # Lazy imports so env validation runs first.
    from google import genai
    from google.genai import types

    # Note: some Gemini publisher models are only available on the "global"
    # Vertex endpoint. If you get NOT_FOUND for a model in a regional location
    # (e.g. us-central1), retry with VERTEX_LOCATION=global.
    client = genai.Client(vertexai=True, project=project_id, location=location)

    repo_root = Path(__file__).resolve().parents[1]
    fixtures_dir = repo_root / "tests" / "fixtures"
    sample_mp3 = fixtures_dir / "sample.mp3"
    sample_jpg = fixtures_dir / "sample.jpg"
    sample_mp4 = fixtures_dir / "sample.mp4"

    # Model candidates (comma-separated env overrides).
    # Defaults are ordered from cheaper → stronger where it makes sense.
    text_models = _split_models(
        os.getenv("SMOKE_TEXT_MODELS", "gemini-2.5-flash-lite,gemini-2.5-flash,gemini-3-flash,gemini-3-flash-preview")
    )
    multimodal_models = _split_models(
        os.getenv("SMOKE_MM_MODELS", "gemini-2.5-flash-lite,gemini-2.5-flash,gemini-3-flash,gemini-3-flash-preview")
    )
    tts_models = _split_models(
        os.getenv("SMOKE_TTS_MODELS", "gemini-2.5-flash-tts,gemini-2.5-flash-preview-tts")
    )

    _print_header("Vertex AI: text probe")
    model_used, resp = _try_models(
        client,
        text_models,
        contents="Say 'ok' and nothing else.",
        label="text",
    )
    print(f"model_used={model_used}")
    print("text:", (resp.text or "").strip())

    _print_header("Vertex AI: audio transcription probe")
    mp3_bytes = _maybe_load_bytes(sample_mp3)
    if not mp3_bytes:
        print(f"SKIP: missing fixture {sample_mp3}")
    else:
        model_used, resp = _try_models(
            client,
            multimodal_models,
            contents=[
                types.Part.from_bytes(data=mp3_bytes, mime_type="audio/mpeg"),
                "Transcribe the audio. Return ONLY the transcript.",
            ],
            label="audio_transcription",
        )
        print(f"model_used={model_used}")
        print("text:", (resp.text or "").strip()[:4000])

    _print_header("Vertex AI: image analysis/OCR probe")
    jpg_bytes = _maybe_load_bytes(sample_jpg)
    if not jpg_bytes:
        print(f"SKIP: missing fixture {sample_jpg}")
    else:
        model_used, resp = _try_models(
            client,
            multimodal_models,
            contents=[
                types.Part.from_bytes(data=jpg_bytes, mime_type="image/jpeg"),
                "Describe the image and extract any readable text.",
            ],
            label="image",
        )
        print(f"model_used={model_used}")
        print("text:", (resp.text or "").strip()[:4000])

    _print_header("Vertex AI: video analysis probe (no files.upload)")
    mp4_bytes = _maybe_load_bytes(sample_mp4)
    if not mp4_bytes:
        print(f"SKIP: missing fixture {sample_mp4}")
    else:
        model_used, resp = _try_models(
            client,
            multimodal_models,
            contents=[
                types.Part.from_bytes(data=mp4_bytes, mime_type="video/mp4"),
                "Summarize the video and transcribe any speech you hear.",
            ],
            label="video",
        )
        print(f"model_used={model_used}")
        print("text:", (resp.text or "").strip()[:4000])

    _print_header("Vertex AI: TTS probe (audio output)")
    model_used, resp = _try_models(
        client,
        tts_models,
        contents="Say: Vertex TTS ok.",
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Kore")
                )
            ),
        ),
        label="tts",
    )

    # Defensive parsing: the SDK returns candidates/parts with inline_data for AUDIO.
    audio_data_b64: str | None = None
    try:
        part = resp.candidates[0].content.parts[0]
        inline = getattr(part, "inline_data", None)
        if inline and getattr(inline, "data", None):
            raw = inline.data
            # Some SDK builds return bytes; normalize to base64 str for printing length.
            if isinstance(raw, bytes):
                audio_data_b64 = base64.b64encode(raw).decode("ascii")
            else:
                audio_data_b64 = str(raw)
    except Exception:
        audio_data_b64 = None

    print(f"model_used={model_used}")
    if not audio_data_b64:
        raise RuntimeError("TTS probe did not return inline audio data.")
    print(f"audio_b64_len={len(audio_data_b64)}")

    print("\nALL DONE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print(f"\nFAIL: {e}", file=sys.stderr)
        return_code = 2
        raise SystemExit(return_code)
