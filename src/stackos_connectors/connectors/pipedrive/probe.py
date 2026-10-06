"""Provider credential-probe construction; no action or lifecycle decisions."""

from .integration import PipedriveIntegration as Integration

__all__ = ["Integration"]

from stackos_connectors.probe import probe_request

from .actions import _base_url


def constructor_kwargs(auth, options):
    return {"api_domain": _base_url(probe_request("pipedrive", auth, options))}
