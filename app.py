"""Entry point for the mcp-services app.

Tina4 auto-discovers route files in ``src/routes/`` — we import them once
here so their @get/@post decorators run before the server starts. Adding
a new integration is a file in ``src/integrations/``; no change needed here.
"""
from tina4_python.dotenv import load_env
load_env()

from tina4_python.core import run

# Side-effect imports: each module registers handlers when loaded.
import src.routes.health      # noqa: F401  /health
import src.routes.landing     # noqa: F401  /
import src.routes.wellknown   # noqa: F401  /.well-known/...
import src.routes.mcp         # noqa: F401  /mcp

run()
