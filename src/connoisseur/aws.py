"""AWS-only helpers, imported lazily so local installs do not need boto3."""
import os

import structlog

# Not connoisseur.log.get_logger: this runs while the settings themselves are loading
log = structlog.get_logger(__name__)


def load_ssm_parameters(path: str, client=None) -> list[str]:
    """Copy every parameter under `path` (e.g. /connoisseur/prod/) into os.environ.

    The last path segment is the variable name: /connoisseur/prod/LANGFUSE_SECRET_KEY -> LANGFUSE_SECRET_KEY.
    Variables already set win, and placeholders left by Terraform ("CHANGE_ME") are skipped.
    Returns the names that were loaded (never the values).
    """
    if client is None:
        import boto3

        client = boto3.client("ssm")
    loaded: list[str] = []
    paginator = client.get_paginator("get_parameters_by_path")
    for page in paginator.paginate(Path=path, WithDecryption=True, Recursive=False):
        for param in page["Parameters"]:
            name = param["Name"].rsplit("/", 1)[-1]
            value = param["Value"]
            if not value or value == "CHANGE_ME" or name in os.environ:
                continue
            os.environ[name] = value
            loaded.append(name)
    log.info("ssm_parameters_loaded", names=sorted(loaded))
    return loaded
