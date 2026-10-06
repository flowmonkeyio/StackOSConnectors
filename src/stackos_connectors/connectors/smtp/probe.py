"""Provider credential-probe construction; no action or lifecycle decisions."""

from .integration import SmtpIntegration as Integration

__all__ = ["Integration"]


def constructor_kwargs(auth, options):
    # Probe constructors own protocol validation. Preserve fractional probe timeouts.
    config = auth.config
    return {
        **{key: config[key] for key in ("host", "tls_mode", "username")},
        "port": int(config["port"]),
        "timeout_s": options.timeout
        if options.timeout is not None
        else float(config.get("timeout_s") or 30),
    }
