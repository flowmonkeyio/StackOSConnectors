"""Provider credential-probe construction; no action or lifecycle decisions."""

from .integration import FtpIntegration as Integration

__all__ = ["Integration"]

from .integration import validate_ftp_credential_config


def constructor_kwargs(auth, options):
    config = auth.config
    validate_ftp_credential_config(config)
    passive = config.get("passive_mode", True)
    return {
        "host": str(config["host"]),
        "port": int(config.get("port") or 21),
        "tls_mode": str(config.get("tls_mode") or "explicit"),
        "username": str(config["username"]),
        "passive_mode": passive
        if isinstance(passive, bool)
        else str(passive).lower() in {"true", "1", "yes", "on"},
        "timeout_s": options.timeout
        if options.timeout is not None
        else float(config.get("timeout_s") or 30),
        "encoding": str(config.get("encoding") or "utf-8"),
    }
