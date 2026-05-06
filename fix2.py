with open('nodes/response_sender.py', 'r') as f:
    text = f.read()

# exact strings to replace with empty string
to_remove = [
    "# Salvar resposta do bot no histórico de chat\n        from nodes.message_handler import save_bot_response_to_history\n        await save_bot_response_to_history(remote_jid, _WELCOME_MESSAGE)\n",
    "# Salvar resposta do bot no hist\u00f3rico de chat\n        from nodes.message_handler import save_bot_response_to_history\n        await save_bot_response_to_history(remote_jid, _RESET_CONFIRMATION_MESSAGE)\n",
    "# Salvar resposta do bot no histórico de chat\n            from nodes.message_handler import save_bot_response_to_history\n            await save_bot_response_to_history(remote_jid, fallback_msg)\n",
    "# Salvar resposta do bot no histórico de chat\n        from nodes.message_handler import save_bot_response_to_history\n        await save_bot_response_to_history(remote_jid, rationale)\n",
    "# Salvar resposta do bot no histórico de chat\n        from nodes.message_handler import save_bot_response_to_history\n        await save_bot_response_to_history(remote_jid, greeting_response)\n",
    "# Salvar resposta do bot no histórico de chat\n        from nodes.message_handler import save_bot_response_to_history\n        await save_bot_response_to_history(remote_jid, unsupported_msg)\n"
]

for r in to_remove:
    text = text.replace(r, "")

with open('nodes/response_sender.py', 'w') as f:
    f.write(text)
