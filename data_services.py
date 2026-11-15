"""Private persistent storage and the Authentik workload's AWS identity."""

import pulumi
import pulumi_aws as aws

import settings as s

provider = aws.Provider(
    "aws", region=s.REGION, allowed_account_ids=[s.account],
    default_tags={"tags": {
        "application": "authentik",
        "pulumi-stack": pulumi.get_stack(),
        "pulumi-project": pulumi.get_project(),
    }},
)
options = pulumi.ResourceOptions(provider=provider)
data_options = pulumi.ResourceOptions(provider=provider, protect=not s.disposable)
cluster = aws.eks.get_cluster(
    name=s.cluster_name, opts=pulumi.InvokeOptions(provider=provider),
)
if cluster.vpc_config.vpc_id != s.vpc_id:
    raise s.ConfigurationError("cluster VPC")
issuer = cluster.identities[0].oidcs[0].issuer.removeprefix("https://")
oidc_arn = f"arn:aws:iam::{s.account}:oidc-provider/{issuer}"

database_group = aws.ec2.SecurityGroup(
    "database", name_prefix=f"{s.prefix}-db-", vpc_id=s.vpc_id,
    description="Authentik PostgreSQL from EKS nodes",
    opts=options,
)
database_ingress = aws.vpc.SecurityGroupIngressRule(
    "database-from-nodes", security_group_id=database_group.id,
    referenced_security_group_id=s.node_security_group,
    ip_protocol="tcp", from_port=5432, to_port=5432, opts=options,
)
subnet_group = aws.rds.SubnetGroup(
    "database", name_prefix=f"{s.prefix}-", subnet_ids=list(s.subnets), opts=options,
)
database = aws.rds.Instance(
    "database", identifier=f"{s.prefix}-db", engine="postgres", engine_version="17.7",
    instance_class="db.t4g.micro", allocated_storage=20, storage_type="gp3",
    storage_encrypted=True, db_name="authentik", username="authentik",
    password=s.db_password, db_subnet_group_name=subnet_group.name,
    vpc_security_group_ids=[database_group.id], publicly_accessible=False,
    backup_retention_period=7, multi_az=False, auto_minor_version_upgrade=False,
    deletion_protection=not s.disposable, skip_final_snapshot=s.disposable,
    final_snapshot_identifier=s.cfg.get("finalSnapshotIdentifier") or f"{s.namespace}-final",
    delete_automated_backups=True, apply_immediately=True,
    opts=pulumi.ResourceOptions.merge(data_options, pulumi.ResourceOptions(
        depends_on=[database_ingress],
    )),
)
bucket = aws.s3.BucketV2(
    "files", bucket_prefix=f"{s.prefix}-files-", force_destroy=s.disposable,
    opts=data_options,
)
public_access = aws.s3.BucketPublicAccessBlock(
    "files", bucket=bucket.id, block_public_acls=True, block_public_policy=True,
    ignore_public_acls=True, restrict_public_buckets=True, opts=options,
)
encryption = aws.s3.BucketServerSideEncryptionConfigurationV2(
    "files", bucket=bucket.id,
    rules=[{"apply_server_side_encryption_by_default": {"sse_algorithm": "AES256"}}],
    opts=options,
)
versioning = aws.s3.BucketVersioningV2(
    "files", bucket=bucket.id, versioning_configuration={"status": "Enabled"},
    opts=options,
)
cors = aws.s3.BucketCorsConfigurationV2(
    "files", bucket=bucket.id,
    cors_rules=[{
        "allowed_origins": ["http://authentik.localhost:9000"],
        "allowed_headers": ["Authorization"], "allowed_methods": ["GET"],
        "max_age_seconds": 3000,
    }], opts=options,
)
transport_policy = aws.s3.BucketPolicy(
    "files-tls", bucket=bucket.id,
    policy=pulumi.Output.json_dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Deny", "Principal": "*", "Action": "s3:*",
            "Resource": [bucket.arn, pulumi.Output.concat(bucket.arn, "/*")],
            "Condition": {"Bool": {"aws:SecureTransport": "false"}},
        }],
    }), opts=options,
)
role = aws.iam.Role(
    "authentik", name_prefix=f"{s.prefix}-",
    permissions_boundary=s.cfg.get("permissionsBoundaryArn"),
    assume_role_policy=pulumi.Output.json_dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow", "Principal": {"Federated": oidc_arn},
            "Action": "sts:AssumeRoleWithWebIdentity",
            "Condition": {"StringEquals": {
                f"{issuer}:aud": "sts.amazonaws.com",
                f"{issuer}:sub": f"system:serviceaccount:{s.namespace}:authentik",
            }},
        }],
    }), opts=options,
)
file_permissions = aws.iam.RolePolicy(
    "files", role=role.id,
    policy=pulumi.Output.json_dumps({
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
             "Resource": bucket.arn},
            {"Effect": "Allow",
             "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject",
                        "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"],
             "Resource": pulumi.Output.concat(bucket.arn, "/*")},
        ],
    }), opts=options,
)
