"""Integration tests for media processing flows.

These tests call real Vertex AI Gemini APIs for transcription / image / video
analysis, so PROJECT_ID/VERTEX_LOCATION/ADC must be set. WhatsApp and fact-checker
calls are mocked since they are not the focus of these tests.
"""

import asyncio
import base64
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import config

# ──────────────────────── fixtures / helpers ────────────────────────

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Minimal valid MP4 (< 2min) — just enough so Gemini can process it.
# To run these tests put a short sample file at tests/fixtures/sample.mp4
# and a sample image at tests/fixtures/sample.jpg
# If the fixture files are missing the corresponding tests are skipped.

_SAMPLE_IMAGE_PATH = FIXTURES_DIR / "sample.jpg"
_SAMPLE_VIDEO_PATH = FIXTURES_DIR / "sample.mp4"
_SAMPLE_AUDIO_PATH = FIXTURES_DIR / "sample.mp3"


def _load_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def _require_vertex_env():
    """Pytest skip helper — skips if Vertex env is not configured."""
    if not config.PROJECT_ID or not config.VERTEX_LOCATION:
        pytest.skip("PROJECT_ID/VERTEX_LOCATION not set — skipping integration test")


def _require_fixture(path: Path):
    if not path.exists():
        pytest.skip(f"Fixture {path.name} not found at {path}")


# Reusable mock for WhatsApp + fact-checker so we never hit those services.
_WA_PATCHES = [
    patch("nodes.whatsapp_api.send_text", new_callable=AsyncMock),
    patch(
        "nodes.whatsapp_api.start_typing_loop",
        new_callable=AsyncMock,
        return_value=AsyncMock(),
    ),
    patch(
        "nodes.whatsapp_api.download_media_as_base64",
        new_callable=AsyncMock,
    ),
]


_DEEPFAKE_RESULT_REQUIRED_KEYS = {"label", "score", "model_used", "media_type", "processing_time_ms"}


def _assert_deepfake_results_schema(results: list[dict]) -> None:
    """Assert each item in the deepfake results list has the expected shape."""
    assert isinstance(results, list)
    assert len(results) > 0
    for item in results:
        assert set(item.keys()) == _DEEPFAKE_RESULT_REQUIRED_KEYS, (
            f"Unexpected keys: {set(item.keys())} — expected {_DEEPFAKE_RESULT_REQUIRED_KEYS}"
        )
        assert item["label"] in ("fake", "real"), f"Invalid label: {item['label']}"
        assert isinstance(item["score"], (int, float)), f"score must be numeric, got {type(item['score'])}"
        assert 0.0 <= item["score"] <= 1.0, f"score out of range: {item['score']}"
        assert isinstance(item["model_used"], str) and len(item["model_used"]) > 0
        assert item["media_type"] in ("video", "audio", "image"), f"Invalid media_type: {item['media_type']}"
        assert isinstance(item["processing_time_ms"], (int, float)) and item["processing_time_ms"] >= 0


def _fact_check_response() -> dict:
    return {"rationale": "test-rationale", "responseWithoutLinks": "test-response"}


def _mock_fact_check_client(response: dict | None = None):
    """Creates a patch for _get_fact_check_client that captures .post() calls.

    Returns (patch_context_manager, mock_post) so tests can use:
        with fc_patch:
            ...
        sent_payload = mock_post.call_args[1]["json"]
    """
    if response is None:
        response = {"rationale": "ok"}

    mock_post = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = response
    mock_post.return_value = mock_resp

    mock_client = MagicMock()
    mock_client.post = mock_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    async def _fake_get_client():
        return mock_client

    fc_patch = patch(
        "nodes.fact_checker._get_fact_check_client",
        side_effect=_fake_get_client,
    )
    return fc_patch, mock_post


# ──────────────────────── ai_services unit-level ────────────────────────


@pytest.mark.asyncio
async def test_transcribe_audio():
    """Transcribe a short audio clip via Gemini."""
    _require_vertex_env()
    _require_fixture(_SAMPLE_AUDIO_PATH)

    from nodes.ai_services import transcribe_audio

    audio_b64 = _load_b64(_SAMPLE_AUDIO_PATH)
    result = await transcribe_audio(audio_b64)

    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_analyze_image_content():
    """Analyze an image via Gemini and check we get a description back."""
    _require_vertex_env()
    _require_fixture(_SAMPLE_IMAGE_PATH)

    from nodes.ai_services import analyze_image_content

    image_b64 = _load_b64(_SAMPLE_IMAGE_PATH)
    result = await analyze_image_content(image_b64)

    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_analyze_video():
    """Analyze a short video via Gemini."""
    _require_vertex_env()
    _require_fixture(_SAMPLE_VIDEO_PATH)

    from nodes.ai_services import analyze_video

    video_b64 = _load_b64(_SAMPLE_VIDEO_PATH)
    result = await analyze_video(video_b64)

    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_detect_deepfake_skipped_when_not_configured():
    """detect_deepfake returns None when DEEP_FAKE_SERVICE_URL is empty."""
    from nodes.ai_services import detect_deepfake

    with patch.object(config, "DEEP_FAKE_SERVICE_URL", ""):
        result = await detect_deepfake(base64.b64encode(b"fake").decode())

    assert result is None


# ──────────────────────── process_image full flow ────────────────────────


@pytest.mark.asyncio
async def test_process_image_full_flow():
    """End-to-end image processing: Gemini analysis + reverse search + fact-check.

    WhatsApp API and fact-checker are mocked; Gemini calls are real.
    """
    _require_vertex_env()
    _require_fixture(_SAMPLE_IMAGE_PATH)

    from nodes.media_processor import process_image

    image_b64 = _load_b64(_SAMPLE_IMAGE_PATH)

    state = {
        "numero_quem_enviou": "5511999999999",
        "id_mensagem": "wamid.test123",
        "media_id": "media-id-test",
        "endpoint_api": "https://fake-factcheck.example.com",
        "caption": "",
    }

    with (
        patch("nodes.whatsapp_api.send_text", new_callable=AsyncMock) as mock_send,
        patch("nodes.whatsapp_api.start_typing_loop", new_callable=AsyncMock, return_value=MagicMock()),
        patch(
            "nodes.whatsapp_api.download_media_as_base64",
            new_callable=AsyncMock,
            return_value=image_b64,
        ),
        patch(
            "nodes.fact_checker.check_content",
            new_callable=AsyncMock,
            return_value=_fact_check_response(),
        ) as mock_fc,
        patch(
            "nodes.ai_services.detect_deepfake",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        result = await process_image(state)

    # Gemini produced a description
    assert "description" in result
    assert len(result["description"]) > 0

    # Fact-checker was called with image content
    mock_fc.assert_awaited_once()
    call_args = mock_fc.call_args
    content_parts = call_args[0][1]
    assert any(p["type"] == "image" for p in content_parts)

    # Status message was sent
    mock_send.assert_awaited()


@pytest.mark.asyncio
async def test_process_image_with_deepfake():
    """Image processing passes deepfake results to fact-checker when available."""
    _require_vertex_env()
    _require_fixture(_SAMPLE_IMAGE_PATH)

    from nodes.media_processor import process_image

    image_b64 = _load_b64(_SAMPLE_IMAGE_PATH)
    fake_deepfake_results = [
        {"label": "fake", "score": 0.85, "model_used": "test-model", "media_type": "image", "processing_time_ms": 100},
    ]

    state = {
        "numero_quem_enviou": "5511999999999",
        "id_mensagem": "wamid.test123",
        "media_id": "media-id-test",
        "endpoint_api": "https://fake-factcheck.example.com",
        "caption": "uma legenda",
    }

    with (
        patch("nodes.whatsapp_api.send_text", new_callable=AsyncMock),
        patch("nodes.whatsapp_api.start_typing_loop", new_callable=AsyncMock, return_value=MagicMock()),
        patch(
            "nodes.whatsapp_api.download_media_as_base64",
            new_callable=AsyncMock,
            return_value=image_b64,
        ),
        patch(
            "nodes.fact_checker.check_content",
            new_callable=AsyncMock,
            return_value=_fact_check_response(),
        ) as mock_fc,
        patch(
            "nodes.ai_services.detect_deepfake",
            new_callable=AsyncMock,
            return_value=fake_deepfake_results,
        ),
    ):
        result = await process_image(state)

    # Fact-checker received deepfake_results kwarg
    mock_fc.assert_awaited_once()
    _, kwargs = mock_fc.call_args
    assert kwargs["deepfake_results"] == fake_deepfake_results

    # Caption was included
    content_parts = mock_fc.call_args[0][1]
    assert any(p["type"] == "text" and p["textContent"] == "uma legenda" for p in content_parts)


# ──────────────────────── process_video full flow ────────────────────────


@pytest.mark.asyncio
async def test_process_video_full_flow():
    """End-to-end video processing: Gemini analysis + fact-check.

    WhatsApp API and fact-checker are mocked; Gemini calls are real.
    """
    _require_vertex_env()
    _require_fixture(_SAMPLE_VIDEO_PATH)

    from nodes.media_processor import process_video

    video_b64 = _load_b64(_SAMPLE_VIDEO_PATH)

    state = {
        "numero_quem_enviou": "5511999999999",
        "id_mensagem": "wamid.test456",
        "media_id": "media-id-video",
        "endpoint_api": "https://fake-factcheck.example.com",
        "caption": "",
    }

    with (
        patch("nodes.whatsapp_api.send_text", new_callable=AsyncMock),
        patch("nodes.whatsapp_api.start_typing_loop", new_callable=AsyncMock, return_value=MagicMock()),
        patch(
            "nodes.whatsapp_api.download_media_as_base64",
            new_callable=AsyncMock,
            return_value=video_b64,
        ),
        patch(
            "nodes.fact_checker.check_content",
            new_callable=AsyncMock,
            return_value=_fact_check_response(),
        ) as mock_fc,
        patch(
            "nodes.ai_services.detect_deepfake",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        result = await process_video(state)

    assert "description" in result
    assert len(result["description"]) > 0

    mock_fc.assert_awaited_once()
    content_parts = mock_fc.call_args[0][1]
    assert any(p["type"] == "video" for p in content_parts)


@pytest.mark.asyncio
async def test_process_video_with_deepfake():
    """Video processing passes deepfake results to fact-checker."""
    _require_vertex_env()
    _require_fixture(_SAMPLE_VIDEO_PATH)

    from nodes.media_processor import process_video

    video_b64 = _load_b64(_SAMPLE_VIDEO_PATH)
    fake_deepfake_results = [
        {"label": "fake", "score": 0.84, "model_used": "frame_sampler", "media_type": "video", "processing_time_ms": 8000},
        {"label": "real", "score": 0.16, "model_used": "frame_sampler", "media_type": "video", "processing_time_ms": 8000},
    ]

    state = {
        "numero_quem_enviou": "5511999999999",
        "id_mensagem": "wamid.test456",
        "media_id": "media-id-video",
        "endpoint_api": "https://fake-factcheck.example.com",
        "caption": "legenda do vídeo",
    }

    with (
        patch("nodes.whatsapp_api.send_text", new_callable=AsyncMock),
        patch("nodes.whatsapp_api.start_typing_loop", new_callable=AsyncMock, return_value=MagicMock()),
        patch(
            "nodes.whatsapp_api.download_media_as_base64",
            new_callable=AsyncMock,
            return_value=video_b64,
        ),
        patch(
            "nodes.fact_checker.check_content",
            new_callable=AsyncMock,
            return_value=_fact_check_response(),
        ) as mock_fc,
        patch(
            "nodes.ai_services.detect_deepfake",
            new_callable=AsyncMock,
            return_value=fake_deepfake_results,
        ),
    ):
        result = await process_video(state)

    mock_fc.assert_awaited_once()
    _, kwargs = mock_fc.call_args
    assert kwargs["deepfake_results"] == fake_deepfake_results


@pytest.mark.asyncio
async def test_process_video_too_long_skips_analysis():
    """Videos over 2 minutes are rejected without calling Gemini."""
    from nodes.media_processor import process_video

    # Craft a state; download will return a base64 that triggers duration >= 120
    # We mock get_video_duration_from_base64 to return 130 seconds
    state = {
        "numero_quem_enviou": "5511999999999",
        "id_mensagem": "wamid.testlong",
        "media_id": "media-id-long",
        "endpoint_api": "https://fake-factcheck.example.com",
        "caption": "",
    }

    dummy_b64 = base64.b64encode(b"not-a-real-video").decode()

    with (
        patch("nodes.whatsapp_api.send_text", new_callable=AsyncMock) as mock_send,
        patch("nodes.whatsapp_api.start_typing_loop", new_callable=AsyncMock, return_value=MagicMock()),
        patch(
            "nodes.whatsapp_api.download_media_as_base64",
            new_callable=AsyncMock,
            return_value=dummy_b64,
        ),
        patch(
            "nodes.media_processor.get_video_duration_from_base64",
            return_value=130.0,
        ),
        patch(
            "nodes.ai_services.analyze_video",
            new_callable=AsyncMock,
        ) as mock_analyze,
        patch(
            "nodes.ai_services.detect_deepfake",
            new_callable=AsyncMock,
        ) as mock_deepfake,
    ):
        result = await process_video(state)

    # Should return empty rationale and the duration
    assert result["rationale"] == ""
    assert result["duration"] == 130.0

    # Gemini and deepfake should NOT have been called
    mock_analyze.assert_not_awaited()
    mock_deepfake.assert_not_awaited()

    # User should get the "too long" message
    assert mock_send.await_count == 2  # status + too-long message


# ──────────────────────── process_audio full flow ────────────────────────


@pytest.mark.asyncio
async def test_process_audio_full_flow():
    """End-to-end audio processing: Gemini transcription + fact-check."""
    _require_vertex_env()
    _require_fixture(_SAMPLE_AUDIO_PATH)

    from nodes.media_processor import process_audio

    audio_b64 = _load_b64(_SAMPLE_AUDIO_PATH)

    state = {
        "numero_quem_enviou": "5511999999999",
        "id_mensagem": "wamid.testaudio",
        "media_id": "media-id-audio",
        "endpoint_api": "https://fake-factcheck.example.com",
    }

    with (
        patch("nodes.whatsapp_api.send_text", new_callable=AsyncMock),
        patch("nodes.whatsapp_api.start_typing_loop", new_callable=AsyncMock, return_value=MagicMock()),
        patch(
            "nodes.whatsapp_api.download_media_as_base64",
            new_callable=AsyncMock,
            return_value=audio_b64,
        ),
        patch(
            "nodes.fact_checker.check_text",
            new_callable=AsyncMock,
            return_value=_fact_check_response(),
        ) as mock_fc,
    ):
        result = await process_audio(state)

    assert "transcription" in result
    assert len(result["transcription"]) > 0

    mock_fc.assert_awaited_once()
    # check_text receives the transcription as second positional arg
    call_args = mock_fc.call_args[0]
    assert len(call_args[1]) > 0  # transcription text


# ──────────────────────── fact_checker payload ────────────────────────


@pytest.mark.asyncio
async def test_check_content_includes_deepfake_in_payload():
    """check_content sends deep-fake-verification-result when provided."""
    from nodes.fact_checker import check_content

    deepfake_results = [
        {"label": "fake", "score": 0.8, "model_used": "test", "media_type": "video", "processing_time_ms": 100},
    ]

    fc_patch, mock_post = _mock_fact_check_client()
    with fc_patch:
        await check_content(
            "https://example.com",
            [{"textContent": "desc", "type": "video"}],
            deepfake_results=deepfake_results,
        )

    sent_payload = mock_post.call_args[1]["json"]
    assert "deep-fake-verification-result" in sent_payload
    assert sent_payload["deep-fake-verification-result"]["results"] == deepfake_results


@pytest.mark.asyncio
async def test_check_content_omits_deepfake_when_none():
    """check_content does NOT include deepfake key when results are None."""
    from nodes.fact_checker import check_content

    fc_patch, mock_post = _mock_fact_check_client()
    with fc_patch:
        await check_content(
            "https://example.com",
            [{"textContent": "desc", "type": "image"}],
        )

    sent_payload = mock_post.call_args[1]["json"]
    assert "deep-fake-verification-result" not in sent_payload


# ──────────────── full payload shape (end-to-end without HTTP) ────────────────


@pytest.mark.asyncio
async def test_process_image_sends_correct_payload_to_backend():
    """Image flow builds the correct JSON payload for the fact-checker backend.

    Gemini and deepfake are mocked with realistic return values.
    check_content is NOT mocked — we intercept at _get_fact_check_client level
    to inspect the actual JSON body that would be sent over the wire.
    """
    from nodes.media_processor import process_image

    dummy_b64 = base64.b64encode(b"fake-image-bytes").decode()
    image_description = "Descrição da imagem: um político em um palanque"
    reverse_search_result = "Entidades Detectadas:\n- Político X\n"
    deepfake_results = [
        {"label": "fake", "score": 0.8392, "model_used": "frame_sampler(prithivMLmods/Deep-Fake-Detector-v2-Model)", "media_type": "image", "processing_time_ms": 8737.68},
        {"label": "real", "score": 0.1608, "model_used": "frame_sampler(prithivMLmods/Deep-Fake-Detector-v2-Model)", "media_type": "image", "processing_time_ms": 8737.68},
    ]

    state = {
        "numero_quem_enviou": "5511999999999",
        "id_mensagem": "wamid.payload-test",
        "media_id": "media-id-test",
        "endpoint_api": "https://fake-factcheck.example.com",
        "caption": "essa imagem é real?",
    }

    fc_patch, mock_post = _mock_fact_check_client({"rationale": "test-rationale"})

    with (
        patch("nodes.whatsapp_api.send_text", new_callable=AsyncMock),
        patch("nodes.whatsapp_api.start_typing_loop", new_callable=AsyncMock, return_value=MagicMock()),
        patch(
            "nodes.whatsapp_api.download_media_as_base64",
            new_callable=AsyncMock,
            return_value=dummy_b64,
        ),
        patch(
            "nodes.ai_services.analyze_image_content",
            new_callable=AsyncMock,
            return_value=image_description,
        ),
        patch(
            "nodes.ai_services.reverse_image_search",
            new_callable=AsyncMock,
            return_value=reverse_search_result,
        ),
        patch(
            "nodes.ai_services.detect_deepfake",
            new_callable=AsyncMock,
            return_value=deepfake_results,
        ),
        fc_patch,
    ):
        await process_image(state)

    sent_payload = mock_post.call_args[1]["json"]

    # Top-level keys
    assert "content" in sent_payload
    assert "deep-fake-verification-result" in sent_payload

    # content array: image description + caption
    content = sent_payload["content"]
    assert len(content) == 2

    image_part = content[0]
    assert image_part["type"] == "image"
    assert image_description in image_part["textContent"]
    assert reverse_search_result in image_part["textContent"]

    caption_part = content[1]
    assert caption_part["type"] == "text"
    assert caption_part["textContent"] == "essa imagem é real?"

    # deep-fake-verification-result schema
    df = sent_payload["deep-fake-verification-result"]
    assert "results" in df
    assert df["results"] == deepfake_results
    _assert_deepfake_results_schema(df["results"])


@pytest.mark.asyncio
async def test_process_video_sends_correct_payload_to_backend():
    """Video flow builds the correct JSON payload for the fact-checker backend.

    Same approach: mock Gemini + deepfake, let check_content run,
    intercept at _get_fact_check_client level.
    """
    from nodes.media_processor import process_video

    dummy_b64 = base64.b64encode(b"fake-video-bytes").decode()
    video_description = "Descrição completa do vídeo: cenas de protesto"
    deepfake_results = [
        {"label": "fake", "score": 0.8392, "model_used": "frame_sampler(prithivMLmods/Deep-Fake-Detector-v2-Model)", "media_type": "video", "processing_time_ms": 8737.68},
        {"label": "real", "score": 0.1608, "model_used": "frame_sampler(prithivMLmods/Deep-Fake-Detector-v2-Model)", "media_type": "video", "processing_time_ms": 8737.68},
        {"label": "fake", "score": 0.0052, "model_used": "VoiceGen (Dual-RawNet2)", "media_type": "audio", "processing_time_ms": 3483.57},
        {"label": "real", "score": 0.9948, "model_used": "VoiceGen (Dual-RawNet2)", "media_type": "audio", "processing_time_ms": 3483.57},
    ]

    state = {
        "numero_quem_enviou": "5511999999999",
        "id_mensagem": "wamid.payload-video",
        "media_id": "media-id-video",
        "endpoint_api": "https://fake-factcheck.example.com",
        "caption": "vídeo suspeito",
    }

    fc_patch, mock_post = _mock_fact_check_client({"rationale": "test-rationale"})

    with (
        patch("nodes.whatsapp_api.send_text", new_callable=AsyncMock),
        patch("nodes.whatsapp_api.start_typing_loop", new_callable=AsyncMock, return_value=MagicMock()),
        patch(
            "nodes.whatsapp_api.download_media_as_base64",
            new_callable=AsyncMock,
            return_value=dummy_b64,
        ),
        patch(
            "nodes.media_processor.get_video_duration_from_base64",
            return_value=30.0,
        ),
        patch(
            "nodes.ai_services.analyze_video",
            new_callable=AsyncMock,
            return_value=video_description,
        ),
        patch(
            "nodes.ai_services.detect_deepfake",
            new_callable=AsyncMock,
            return_value=deepfake_results,
        ),
        fc_patch,
    ):
        await process_video(state)

    sent_payload = mock_post.call_args[1]["json"]

    # Top-level keys
    assert "content" in sent_payload
    assert "deep-fake-verification-result" in sent_payload

    # content array: video description + caption
    content = sent_payload["content"]
    assert len(content) == 2

    video_part = content[0]
    assert video_part["type"] == "video"
    assert video_part["textContent"] == video_description

    caption_part = content[1]
    assert caption_part["type"] == "text"
    assert caption_part["textContent"] == "vídeo suspeito"

    # deep-fake-verification-result schema
    df = sent_payload["deep-fake-verification-result"]
    assert "results" in df
    assert len(df["results"]) == 4
    assert df["results"] == deepfake_results
    _assert_deepfake_results_schema(df["results"])


@pytest.mark.asyncio
async def test_process_image_no_deepfake_no_caption_payload():
    """Image flow without deepfake and without caption — payload has no extras."""
    from nodes.media_processor import process_image

    dummy_b64 = base64.b64encode(b"fake-image").decode()

    state = {
        "numero_quem_enviou": "5511999999999",
        "id_mensagem": "wamid.minimal",
        "media_id": "media-id-test",
        "endpoint_api": "https://fake-factcheck.example.com",
        "caption": "",
    }

    fc_patch, mock_post = _mock_fact_check_client()

    with (
        patch("nodes.whatsapp_api.send_text", new_callable=AsyncMock),
        patch("nodes.whatsapp_api.start_typing_loop", new_callable=AsyncMock, return_value=MagicMock()),
        patch(
            "nodes.whatsapp_api.download_media_as_base64",
            new_callable=AsyncMock,
            return_value=dummy_b64,
        ),
        patch(
            "nodes.ai_services.analyze_image_content",
            new_callable=AsyncMock,
            return_value="image desc",
        ),
        patch(
            "nodes.ai_services.reverse_image_search",
            new_callable=AsyncMock,
            return_value="reverse result",
        ),
        patch(
            "nodes.ai_services.detect_deepfake",
            new_callable=AsyncMock,
            return_value=None,
        ),
        fc_patch,
    ):
        await process_image(state)

    sent_payload = mock_post.call_args[1]["json"]

    # Only content, no deepfake key
    assert "content" in sent_payload
    assert "deep-fake-verification-result" not in sent_payload

    # Only image part, no caption part
    assert len(sent_payload["content"]) == 1
    assert sent_payload["content"][0]["type"] == "image"
