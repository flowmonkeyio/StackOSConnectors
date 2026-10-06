"""Provider credential-probe construction; no action or lifecycle decisions."""

from .integration import SlackBotIntegration as Integration

__all__ = ["Integration"]


def constructor_kwargs(auth, options):
    return (
        {"api_base_url": str(auth.config["api_base_url"])}
        if auth.config.get("api_base_url")
        else {}
    )
