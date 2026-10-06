"""Provider credential-probe construction; no action or lifecycle decisions."""

from .integration import ImapIntegration as Integration

__all__ = ["Integration"]


def constructor_kwargs(auth, options):
    # The caller selects a native mailbox, not a host mailbox ref or policy object.
    config = auth.config
    mailbox = options.provider_context.get("mailbox", "INBOX")
    if not isinstance(mailbox, str):
        raise ValueError("probe mailbox must be text")
    return {
        **{key: config[key] for key in ("host", "tls_mode", "username")},
        "port": int(config["port"]),
        "default_mailbox": mailbox,
        "tls_ca_pem": config.get("tls_ca_pem"),
        "timeout_s": options.timeout
        if options.timeout is not None
        else float(config.get("timeout_s") or 30),
    }
