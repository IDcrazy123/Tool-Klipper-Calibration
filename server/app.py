"""
Compatibility entrypoint for tool_calibrator_server.
Exposes Flask app, detection routines, and main runner.
"""

import sys
import os

# Ensure server package can be imported directly
sys.path.insert(0, os.path.dirname(__file__))

from tool_calibrator_server import *

if __name__ == "__main__":
    main()
