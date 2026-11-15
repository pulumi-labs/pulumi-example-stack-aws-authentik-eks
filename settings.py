"""Parse the operator configuration once, before resource registration."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pulumi

ROOT: Final = Path(__file__).resolve().parent
REGION: Final = "us-east-2"
cfg: Final = pulumi.Config()
stack: Final = pulumi.get_stack()


@dataclass(frozen=True, slots=True)
class ConfigurationError(ValueError):
    """A configuration field violates the deployment contract."""

    field: str

    def __str__(self) -> str:
        return f"Invalid {self.field}; see README configuration requirements"


if not re.fullmatch(r"[a-z][a-z0-9-]{0,23}", stack):
    raise ConfigurationError("stack name")

prefix: Final = cfg.get("resourcePrefix") or f"authentik-{stack}"
if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", prefix):
    raise ConfigurationError("resourcePrefix")
namespace: Final = prefix
account: Final = cfg.require("accountId")
cluster_name: Final = cfg.require("clusterName")
vpc_id: Final = cfg.require("vpcId")
node_security_group: Final = cfg.require("nodeSecurityGroupId")
subnets: Final = tuple(cfg.require("privateSubnetIds").split(","))
disposable: Final = cfg.require_bool("disposable")
server_replicas: Final = cfg.require_int("serverReplicas")
worker_replicas: Final = cfg.require_int("workerReplicas")

if not re.fullmatch(r"\d{12}", account):
    raise ConfigurationError("accountId")
if not re.fullmatch(r"vpc-[0-9a-f]+", vpc_id):
    raise ConfigurationError("vpcId")
if not re.fullmatch(r"sg-[0-9a-f]+", node_security_group):
    raise ConfigurationError("nodeSecurityGroupId")
if len(set(subnets)) < 2 or not all(
    re.fullmatch(r"subnet-[0-9a-f]+", subnet) for subnet in subnets
):
    raise ConfigurationError("privateSubnetIds")
if not 1 <= server_replicas <= 4 or not 1 <= worker_replicas <= 4:
    raise ConfigurationError("replica counts")


def secret(name: str) -> pulumi.Output[str]:
    """Parse secret input without including its value in diagnostics."""
    def parse(value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", value):
            raise ConfigurationError(name)
        return value

    return cfg.require_secret(name).apply(parse)


db_password: Final = secret("databasePassword")
secret_key: Final = secret("secretKey")
bootstrap_password: Final = secret("bootstrapPassword")
alice_password: Final = secret("alicePassword")
bob_password: Final = secret("bobPassword")
client_secret: Final = secret("clientSecret")
cookie_secret: Final = cfg.require_secret("cookieSecret")


def parse_cookie_secret(value: str) -> str:
    """Parse the URL-safe base64 encoding of a 32-byte encryption key."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}=", value):
        raise ConfigurationError("cookieSecret")
    return value


cookie_key: Final = cookie_secret.apply(parse_cookie_secret)
