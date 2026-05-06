import re

with open("nodes/media_processor.py", "r") as f:
    content = f.read()

# Remove process_text log block
content = re.sub(
    r'    logger\.info\(\n\s*"process_text:.*?\)[\n]*',
    '',
    content,
    flags=re.DOTALL
)

# Remove process_video log block
content = re.sub(
    r'    logger\.info\(\n\s*"process_video:.*?\)[\n]*',
    '',
    content,
    flags=re.DOTALL
)

# Add logs for audio transcription
content = re.sub(
    r'        if config\.DEEP_FAKE_AUDIO:\n\s*transcription, deepfake_results.*?\n\s*\)\n\s*else:\n\s*transcription = await ai_services.transcribe_audio\(audio_b64\)\n\s*deepfake_results = None\n\s*except Exception as e:\n\s*logger\.exception\("Falha ao transcrever áudio"\)',
    '',
    content, # Wait, I can't do this easily with regex.
)
