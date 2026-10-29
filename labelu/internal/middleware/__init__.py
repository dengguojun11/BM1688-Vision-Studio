from fastapi import FastAPI

from labelu.internal.middleware.content_type import ContentTypeMiddleware
from labelu.internal.middleware.frontend_dev_proxy import FrontendDevProxyMiddleware
from labelu.internal.middleware.local_dev_cache import LocalDevCacheMiddleware
from labelu.internal.middleware.tracing import TracingMiddleWare


def add_middleware(app: FastAPI):
    app.add_middleware(TracingMiddleWare)
    app.add_middleware(ContentTypeMiddleware)
    app.add_middleware(FrontendDevProxyMiddleware)
    app.add_middleware(LocalDevCacheMiddleware)
