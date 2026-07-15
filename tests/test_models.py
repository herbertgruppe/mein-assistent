"""
Test verschiedene Modellnamen
"""

import os
from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage

load_dotenv()

# Verschiedene Modellnamen testen
model_names = [
    "claude-3-5-sonnet-20241022",
    "claude-3-5-sonnet-latest",
    "claude-3-5-sonnet",
    "claude-3-sonnet-20240229",
    "claude-sonnet-4-5",
    "claude-opus-4-5-20251101"
]

api_key = os.getenv("ANTHROPIC_API_KEY")

print("🔍 Teste verschiedene Modellnamen...\n")

for model_name in model_names:
    try:
        print(f"Teste: {model_name} ... ", end="")
        llm = ChatAnthropic(api_key=api_key, model=model_name, temperature=0.7)
        response = llm.invoke([HumanMessage(content="Hi")])
        print(f"✓ FUNKTIONIERT")
        break
    except Exception as e:
        if "404" in str(e) or "not_found" in str(e):
            print(f"✗ Nicht gefunden")
        else:
            print(f"✗ Fehler: {str(e)[:50]}")
