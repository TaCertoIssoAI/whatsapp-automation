import re

with open("nodes/ai_services.py", "r") as f:
    text = f.read()

# Pattern to remove everything from 'Google Cloud Vision — Reverse Image Search'
# up to the end of the file. Looking at the code it's from line 299 to the end.
text = re.sub(r'# ──────────────────────── Google Cloud Vision — Reverse Image Search ──────.*', '', text, flags=re.DOTALL)

with open("nodes/ai_services.py", "w") as f:
    f.write(text.strip() + "\n")
