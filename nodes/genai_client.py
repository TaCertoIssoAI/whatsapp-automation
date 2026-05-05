"""GenAI client factory.

This repo uses Vertex AI (ADC) rather than the Gemini Developer API key flow.
"""

from __future__ import annotations

import config


def get_genai_client():
    """Return a google-genai client configured for Vertex AI."""
    from google import genai

    return genai.Client(
        vertexai=True,
        project=config.PROJECT_ID,
        location=config.VERTEX_LOCATION,
    )

