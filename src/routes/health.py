"""k8s liveness/readiness probe. Unauthenticated by design."""
from tina4_python.core.router import get, noauth


@noauth()
@get("/health")
async def health(request, response):
    return response({"ok": True, "service": "hello-mcp"})
