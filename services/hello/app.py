"""Entry point for the hello-mcp service.

Tina4-Python boots automatically from the imports in src/routes/. Each route
module registers its handlers at import time. We import them once here so
the framework picks them up before the server starts.
"""
from tina4_python.dotenv import load_env
load_env()

from tina4_python.core import run

# Side-effect imports: each module registers @get/@post handlers when loaded.
import src.routes.health      # noqa: F401  /health
import src.routes.wellknown   # noqa: F401  /.well-known/...
import src.routes.mcp         # noqa: F401  /mcp

run()
