import re

with open("nodes/media_processor.py", "r") as f:
    text = f.read()

# For audio
audio_replace = """    except Exception as e:
        logger.error(f"Falha ao transcrever áudio media_id={media_id}: {e}")
        await _send_error(remote_jid, msg_id, "Não consegui transcrever o áudio.")
        return {"rationale": "", "error_sent": True}

    logger.info(f"Transcrição do áudio {media_id[:20]}: {transcription[:100]}...")"""

text = re.sub(
    r'    except Exception:\n\s*logger\.exception\("Falha ao transcrever áudio"\)\n\s*await _send_error\(remote_jid, msg_id, "Não consegui transcrever o áudio\."\)\n\s*return {"rationale": "", "error_sent": True}  # type: ignore\[return-value\]',
    audio_replace,
    text
)

# For image
image_replace = """    except Exception as e:
        logger.error(f"Falha ao analisar imagem media_id={media_id}: {e}")
        await _send_error(remote_jid, msg_id, "Não consegui analisar a imagem.")
        return {"rationale": "", "error_sent": True}

    logger.info(f"Análise da imagem {media_id[:20]}: {image_analysis[:100]}...")"""

text = re.sub(
    r'    except Exception:\n\s*logger\.exception\("Falha ao analisar imagem"\)\n\s*await _send_error\(remote_jid, msg_id, "Não consegui analisar a imagem\."\)\n\s*return {"rationale": "", "error_sent": True}  # type: ignore\[return-value\]',
    image_replace,
    text
)

# For video
video_replace = """    except Exception as e:
        logger.error(f"Falha ao analisar vídeo media_id={media_id}: {e}")
        await _send_error(remote_jid, msg_id, "Não consegui analisar o vídeo.")
        return {"rationale": "", "error_sent": True}

    logger.info(f"Análise do vídeo {media_id[:20]}: {description[:100]}...")"""

text = re.sub(
    r'    except Exception:\n\s*logger\.exception\("Falha ao analisar vídeo"\)\n\s*await _send_error\(remote_jid, msg_id, "Não consegui analisar o vídeo\."\)\n\s*return {"rationale": "", "error_sent": True}  # type: ignore\[return-value\]',
    video_replace,
    text
)

# For fact_check_text
text = re.sub(
    r'    except Exception:\n\s*logger\.exception\("Falha no fact-check do texto"\)',
    r'    except Exception as e:\n        logger.error(f"Falha no fact-check do texto: {e}")',
    text
)

text = re.sub(
    r'    except Exception:\n\s*logger\.exception\("Falha no fact-check do áudio"\)',
    r'    except Exception as e:\n        logger.error(f"Falha no fact-check do áudio media_id={media_id}: {e}")',
    text
)

text = re.sub(
    r'    except Exception:\n\s*logger\.exception\("Falha no fact-check da imagem"\)',
    r'    except Exception as e:\n        logger.error(f"Falha no fact-check da imagem media_id={media_id}: {e}")',
    text
)

text = re.sub(
    r'    except Exception:\n\s*logger\.exception\("Falha no fact-check do vídeo"\)',
    r'    except Exception as e:\n        logger.error(f"Falha no fact-check do vídeo media_id={media_id}: {e}")',
    text
)

with open("nodes/media_processor.py", "w") as f:
    f.write(text)
