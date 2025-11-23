import re
def extract_json(text: str) -> str:
    m = re.search(r'\{.*\}', text, re.S)
    return m.group(0).strip() if m else '{"face_detected": false}'
