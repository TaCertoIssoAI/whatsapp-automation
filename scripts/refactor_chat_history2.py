import re

with open('nodes/message_handler.py', 'r') as f:
    content = f.read()

# remove calls to _add_to_chat_history in handle_incoming_message
content = re.sub(
    r'\n\s*# Salvar no histórico de chat[\s\S]*?await _add_to_chat_history[^\n]*\n', 
    '\n', 
    content
)

# remove save_bot_response_to_history at the end
content = re.sub(r'async def save_bot_response_to_history[\s\S]*?(?=$|\n#)', '', content)

# remove from router.py or other usages?
with open('nodes/message_handler.py', 'w') as f:
    f.write(content)
