#!/usr/bin/env python3
"""
Test für die ResearchAgent-Fixes
"""

import os
from dotenv import load_dotenv

# Lade Umgebungsvariablen
load_dotenv()

# Test 1: DocumentTool mit leerem String
print("=" * 60)
print("TEST 1: DocumentTool mit leerem Suchstring")
print("=" * 60)

from tools import DocumentTool

doc_tool = DocumentTool()
result = doc_tool.invoke("")
print(f"\nErgebnis:\n{result}\n")

# Test 2: DocumentTool mit kurzem String
print("=" * 60)
print("TEST 2: DocumentTool mit kurzem Suchstring")
print("=" * 60)

result = doc_tool.invoke("a")
print(f"\nErgebnis:\n{result}\n")

# Test 3: DocumentTool mit valider Suche
print("=" * 60)
print("TEST 3: DocumentTool mit validem Suchstring 'Herbert'")
print("=" * 60)

result = doc_tool.invoke("Herbert")
print(f"\nErgebnis:\n{result}\n")

print("=" * 60)
print("TESTS ABGESCHLOSSEN")
print("=" * 60)
