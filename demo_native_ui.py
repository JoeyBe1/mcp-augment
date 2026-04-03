#!/usr/bin/env python3
"""
Final ‘Smart Field Editor’ Demo (No JSON for humans).
Triggers a simulated review flow to verify the new multi-field picker.
"""

import json
import os
import sys
from pathlib import Path

# Setup paths to import MCAugmentMCP
_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT / "project-tools" / "mcp-hooks-server"))
from importlib.machinery import SourceFileLoader

_mod = SourceFileLoader(
    "mcp_augment",
    str(_ROOT / "project-tools" / "mcp-hooks-server" / "mcp-augment.py"),
).load_module()
MCAugmentMCP = _mod.MCAugmentMCP

def main():
    print("🚀 Starting mcp-augment SMART FIELD EDITOR Demo...")
    
    # Initialize server in a temp dir
    import tempfile
    with tempfile.TemporaryDirectory() as tmp_dir:
        server = MCAugmentMCP()
        server._config_loaded = True
        server._cached_config = {"settings": {}}
        server.project_dir = tmp_dir
        os.makedirs(os.path.join(tmp_dir, ".claude/logs"), exist_ok=True)
        server.log_file = os.path.join(tmp_dir, ".claude/logs/mcp-augment.log")
        
        print("\n--- TEST: LOOPING SMART PICKER (V10) ---")
        print("1. Click 'Edit' in the first popup.")
        print("2. Choose '1. EDIT MESSAGE' from the list.")
        print("3. Edit the text in the box, click 'Save'.")
        print("4. You will see the LIST again. (The loop!)")
        print("5. Choose '✅ DONE (Finish Editing)' to exit.")
        
        proposed = {"command": "echo 'Final Looping V10 Verification'", "path": "/Users/joey/proj/"}
        instructions = "Testing the Looping Picker Flow (V10)."
        
        result = server._run_review_envelope(
            phase="tool_input",
            original={"command": "echo 'old'"},
            proposed=proposed,
            instructions=instructions,
            title="Premium Smart Edit Verification"
        )
        
        print(f"\n✅ Resulting Tool Call: {json.dumps(result, indent=2)}")
        
        if not result:
            print("🛑 Action was DECLINED.")
        elif result == proposed:
            print("🎉 Action was ACCEPTED as-is.")
        else:
            print("✏️ Success! You edited only the TEXT, and I merged the JSON for you.")

if __name__ == "__main__":
    main()
