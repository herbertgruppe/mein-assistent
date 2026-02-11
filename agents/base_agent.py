"""
Basis-Klasse für alle Agenten
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List


class BaseAgent(ABC):
    """Basis-Klasse für alle Agenten im System"""

    def __init__(self, name: str):
        self.name = name
        self.memory: List[Dict[str, Any]] = []

    @abstractmethod
    def process(self, input_data: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Verarbeitet die Eingabedaten und gibt ein Ergebnis zurück

        Args:
            input_data: Die zu verarbeitenden Eingabedaten
            context: Optionaler Kontext aus vorherigen Agenten

        Returns:
            Dict mit dem Ergebnis der Verarbeitung
        """
        pass

    def add_to_memory(self, entry: Dict[str, Any]) -> None:
        """Fügt einen Eintrag zum Agent-Gedächtnis hinzu"""
        self.memory.append(entry)

    def get_memory(self) -> List[Dict[str, Any]]:
        """Gibt das Gedächtnis des Agenten zurück"""
        return self.memory

    def clear_memory(self) -> None:
        """Löscht das Gedächtnis des Agenten"""
        self.memory.clear()
