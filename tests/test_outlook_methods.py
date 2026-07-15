"""
Test für neue OutlookGraphTool-Methoden (ohne echte API-Calls)
"""

import inspect
from tools.outlook_graph_tool import OutlookGraphTool

print("🧪 Teste OutlookGraphTool neue Methoden...")

# Initialisiere Tool
tool = OutlookGraphTool()

# Prüfe ob neue Methoden existieren
required_methods = [
    'get_unread_emails',
    'mark_as_read',
    'move_to_folder',
    'forward_email'
]

print("\n✓ Prüfe Methoden-Existenz:")
for method_name in required_methods:
    if hasattr(tool, method_name):
        method = getattr(tool, method_name)
        sig = inspect.signature(method)
        print(f"  ✓ {method_name}{sig}")
    else:
        print(f"  ❌ {method_name} fehlt!")
        exit(1)

# Prüfe Methoden-Signaturen
print("\n✓ Prüfe Methoden-Signaturen:")

# get_unread_emails
sig = inspect.signature(tool.get_unread_emails)
params = list(sig.parameters.keys())
assert 'max_results' in params, "get_unread_emails muss max_results Parameter haben"
assert 'folder' in params, "get_unread_emails muss folder Parameter haben"
print("  ✓ get_unread_emails: korrekte Signatur")

# mark_as_read
sig = inspect.signature(tool.mark_as_read)
params = list(sig.parameters.keys())
assert 'email_id' in params, "mark_as_read muss email_id Parameter haben"
print("  ✓ mark_as_read: korrekte Signatur")

# move_to_folder
sig = inspect.signature(tool.move_to_folder)
params = list(sig.parameters.keys())
assert 'email_id' in params, "move_to_folder muss email_id Parameter haben"
assert 'folder_name' in params, "move_to_folder muss folder_name Parameter haben"
print("  ✓ move_to_folder: korrekte Signatur")

# forward_email
sig = inspect.signature(tool.forward_email)
params = list(sig.parameters.keys())
assert 'email_id' in params, "forward_email muss email_id Parameter haben"
assert 'to_recipients' in params, "forward_email muss to_recipients Parameter haben"
assert 'comment' in params, "forward_email muss comment Parameter haben"
print("  ✓ forward_email: korrekte Signatur")

# Teste Rückgabewerte ohne Authentifizierung
print("\n✓ Teste Rückgabewerte ohne Authentifizierung:")

# get_unread_emails sollte leere Liste zurückgeben
result = tool.get_unread_emails(max_results=5)
assert isinstance(result, list), "get_unread_emails muss Liste zurückgeben"
print("  ✓ get_unread_emails: gibt Liste zurück")

# mark_as_read sollte Dict mit success=False zurückgeben
result = tool.mark_as_read("test123")
assert isinstance(result, dict), "mark_as_read muss Dict zurückgeben"
assert 'success' in result, "mark_as_read muss 'success' Key haben"
print("  ✓ mark_as_read: gibt Dict mit 'success' zurück")

# move_to_folder sollte Dict mit success=False zurückgeben
result = tool.move_to_folder("test123", "Archive")
assert isinstance(result, dict), "move_to_folder muss Dict zurückgeben"
assert 'success' in result, "move_to_folder muss 'success' Key haben"
print("  ✓ move_to_folder: gibt Dict mit 'success' zurück")

# forward_email sollte Dict mit success=False zurückgeben
result = tool.forward_email("test123", ["test@example.com"], "Test comment")
assert isinstance(result, dict), "forward_email muss Dict zurückgeben"
assert 'success' in result, "forward_email muss 'success' Key haben"
print("  ✓ forward_email: gibt Dict mit 'success' zurück")

print("\n✅ Alle Methoden-Tests bestanden!")
print("\nℹ️ Für echte API-Tests muss Outlook authentifiziert sein.")
