import re

with open('nodes/message_handler.py', 'r') as f:
    content = f.read()

# Remove chat_history functions definitions
content = re.sub(r'def _chat_history_key[\s\S]*?(?=\nasync def _call_gemini_classifier|# ═══════════════════════════════════════════════════════════════════\n#  Formatação de mensagens para prompts)', '', content)

with open('nodes/message_handler.py', 'w') as f:
    f.write(content)
