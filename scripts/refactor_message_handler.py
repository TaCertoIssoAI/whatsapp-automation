import re

with open('nodes/message_handler.py', 'r') as f:
    content = f.read()

# Remove the prompts
prompt_start = content.find('_CLASSIFIER_PROMPT = """')
classifier_start = content.find('async def _call_gemini_classifier')
if prompt_start != -1 and classifier_start != -1:
    content = content[:prompt_start] + content[classifier_start:]

# Remove _call_gemini_classifier and _call_gemini_chat
classifier_start = content.find('async def _call_gemini_classifier')
formatting_start = content.find('# ═══════════════════════════════════════════════════════════════════\n#  Formatação de mensagens para prompts')
if classifier_start != -1 and formatting_start != -1:
    content = content[:classifier_start] + content[formatting_start:]

# Replace the block inside _classify_and_act
batch_texto_start = content.find('# ══════════════════════════════════════════\n        #  BATCH SÓ TEXTO → classificar com Gemini')
handle_batch_with_media_start = content.find('async def _handle_batch_with_media')

if batch_texto_start != -1 and handle_batch_with_media_start != -1:
    replacement = """# ══════════════════════════════════════════
        #  BATCH SÓ TEXTO → verificação direta
        # ══════════════════════════════════════════
        
        # Renovar typing indicator
        last_msg_id = pending[-1].get("msg_id", "") if pending else ""
        if last_msg_id:
            try:
                await whatsapp_api.send_typing_indicator(last_msg_id)
            except Exception:
                pass

        logger.info("[classify] Decisão padrão: VERIFICAR (LLM desativado) para %s", phone[-4:])
        await _handle_verify(phone, pending, process_message_callback)
        return

"""
    # Find the last 'return' and empty lines before handle_batch_with_media
    # The block inside the attempt loop ends right before the module level 'async def _handle_batch_with_media'
    # Actually wait! The end of `_classify_and_act` is precisely before `async def _handle_batch_with_media`.
    
    # We will replace from batch_texto_start up to the line before `async def _handle_batch_with_media`
    content = content[:batch_texto_start] + replacement + content[handle_batch_with_media_start - 2:] # keep some empty lines

with open('nodes/message_handler.py', 'w') as f:
    f.write(content)
print("Done refactoring")
