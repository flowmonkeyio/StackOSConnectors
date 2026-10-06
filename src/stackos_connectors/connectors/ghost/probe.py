"""Provider credential-probe construction; no action or lifecycle decisions."""

from .integration import GhostIntegration as Integration

__all__ = ["Integration"]


def project_config(config):
    projected = dict(config)
    if site_url := config.get("ghost_url") or config.get("site_url") or config.get("base_url"):
        projected["ghost_url"] = str(site_url)
    return projected


def constructor_kwargs(auth, options):
    return {
        "site_url": auth.config["ghost_url"],
        **({"api_version": auth.config["api_version"]} if auth.config.get("api_version") else {}),
    }
