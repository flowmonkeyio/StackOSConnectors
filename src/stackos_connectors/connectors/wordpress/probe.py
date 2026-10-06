"""Provider credential-probe construction; no action or lifecycle decisions."""

from .integration import WordPressIntegration as Integration

__all__ = ["Integration"]


def project_config(config):
    projected = dict(config)
    if site_url := config.get("wp_url") or config.get("site_url") or config.get("base_url"):
        projected["wp_url"] = str(site_url)
    return projected


def constructor_kwargs(auth, options):
    return {"site_url": auth.config["wp_url"]}
