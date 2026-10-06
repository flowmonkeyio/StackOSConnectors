"""Provider credential-probe construction; no action or lifecycle decisions."""

from .integration import OpenRouterIntegration as Integration

__all__ = ["Integration"]


def constructor_kwargs(auth, options):
    return {
        key: auth.config[key].strip()
        for key in ("http_referer", "app_title")
        if isinstance(auth.config.get(key), str) and auth.config[key].strip()
    }
