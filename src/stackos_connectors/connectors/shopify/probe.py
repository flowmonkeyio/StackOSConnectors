"""Provider credential-probe construction; no action or lifecycle decisions."""

from .integration import ShopifyIntegration as Integration

__all__ = ["Integration"]


def project_config(config):
    projected = dict(config)
    if domain := config.get("store_domain") or config.get("shop_domain") or config.get("shop"):
        projected["store_domain"] = str(domain)
    return projected


def constructor_kwargs(auth, options):
    return {
        "store_domain": auth.config["store_domain"],
        **({"api_version": auth.config["api_version"]} if auth.config.get("api_version") else {}),
    }
