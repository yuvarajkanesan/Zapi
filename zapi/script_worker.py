"""`python -m zapi.script_worker`: the script engine's helper process (see scripting.worker_main)."""
import sys

from zapi import scripting

if __name__ == "__main__":
    sys.exit(scripting.worker_main())
