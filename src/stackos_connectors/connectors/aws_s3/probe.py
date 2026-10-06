"""Provider credential-probe construction; no action or lifecycle decisions."""

from .integration import S3Integration as Integration

__all__ = ["Integration"]

from .integration import validate_s3_credential_config


def constructor_kwargs(auth, options):
    validate_s3_credential_config(auth.config)
    return {
        "bucket": str(auth.config["bucket"]),
        "region": str(auth.config["region"]),
        "prefix": str(auth.config.get("prefix") or ""),
    }
