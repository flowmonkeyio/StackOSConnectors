"""Provider credential-probe construction; no action or lifecycle decisions."""

from .integration import DataForSeoIntegration as Integration

__all__ = ["Integration"]


def constructor_kwargs(auth, options):
    return {"login": auth.config["login"]}
