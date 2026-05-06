import re

with open('nodes/response_sender.py', 'r') as f:
    content = f.read()

# remove from nodes.message_handler import save_bot_response_to_history
content = re.sub(r'\n\s*from nodes\.message_handler import save_bot_response_to_history\n\s*await save_bot_response_to_history[^\n]*\n', '\n', content)

# just in case comments are there 
content = re.sub(r'# Salvar resposta do bot no histórico de chat\n', '', content)
content = re.sub(r'# Salvar resposta do bot no hist.*chat\n', '', content)

with open('nodes/response_sender.py', 'w') as f:
    f.write(content)
