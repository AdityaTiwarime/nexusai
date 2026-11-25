# Security rules for the AWS deployment, checked without an AWS account.
# The providers are mocked, so nothing is created and no credentials are needed:
#   tofu init && tofu test        (or: terraform init && terraform test)

mock_provider "aws" {
  mock_data "aws_availability_zones" {
    defaults = { names = ["ap-south-1a", "ap-south-1b"] }
  }
  mock_data "aws_ssm_parameter" {
    defaults = { insecure_value = "ami-0123456789abcdef0" }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_resource "aws_secretsmanager_secret" {
    defaults = { arn = "arn:aws:secretsmanager:ap-south-1:123456789012:secret:nexusai/app-abc" }
  }
  mock_resource "aws_kms_key" {
    defaults = { arn = "arn:aws:kms:ap-south-1:123456789012:key/11111111-2222-3333-4444-555555555555" }
  }
  mock_resource "aws_s3_bucket" {
    defaults = {
      arn    = "arn:aws:s3:::nexusai-backups-abc"
      bucket = "nexusai-backups-abc"
    }
  }
  mock_resource "aws_instance" {
    defaults = { arn = "arn:aws:ec2:ap-south-1:123456789012:instance/i-0123456789abcdef0" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:ap-south-1:123456789012:log-group:/nexusai/vpc-flow-logs" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/nexusai-role" }
  }
  mock_resource "aws_iam_openid_connect_provider" {
    defaults = { arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com" }
  }
  mock_resource "aws_ebs_volume" {
    defaults = { id = "vol-0123456789abcdef0" }
  }
}

mock_provider "random" {
  # Real base64 containing "+" and "/" so the URL-safe conversion is exercised.
  mock_resource "random_bytes" {
    defaults = { base64 = "Jc0lAI/zyC6wc/ene/+H9XV6FqB1Nye69IomyBwPXJI=" }
  }
}

variables {
  domain_name    = "nexusai.example.com"
  acme_email     = "ops@example.com"
  repo_url       = "https://github.com/example/nexusai.git"
  image_registry = "ghcr.io/example"
  image_tag      = "0123456789abcdef0123456789abcdef01234567"
}

run "only_the_website_is_reachable" {
  assert {
    condition = toset([
      for r in [aws_vpc_security_group_ingress_rule.https, aws_vpc_security_group_ingress_rule.http3, aws_vpc_security_group_ingress_rule.http] :
      "${r.ip_protocol}/${r.from_port}-${r.to_port}"
    ]) == toset(["tcp/443-443", "udp/443-443", "tcp/80-80"])
    error_message = "Only 80/443 may be open to the internet (no SSH, no database, no API port)."
  }
  assert {
    condition     = aws_vpc_security_group_egress_rule.https.from_port == 443 && aws_vpc_security_group_egress_rule.https.to_port == 443
    error_message = "Outbound traffic must be limited to HTTPS."
  }
  assert {
    condition     = length(aws_default_security_group.default.ingress) == 0 && length(aws_default_security_group.default.egress) == 0
    error_message = "The VPC's default security group must have no rules."
  }
}

run "server_hardening" {
  assert {
    condition     = aws_instance.app.metadata_options[0].http_tokens == "required"
    error_message = "IMDSv2 must be required."
  }
  assert {
    condition     = aws_instance.app.metadata_options[0].http_put_response_hop_limit == 1
    error_message = "Metadata hop limit must be 1 so containers can't read the instance credentials."
  }
  assert {
    condition     = aws_instance.app.root_block_device[0].encrypted && aws_ebs_volume.data.encrypted
    error_message = "All disks must be encrypted."
  }
  assert {
    condition     = aws_instance.app.associate_public_ip_address != true && aws_subnet.public.map_public_ip_on_launch == false
    error_message = "The only public address is the elastic IP."
  }
}

run "secrets_never_appear_in_user_data" {
  assert {
    condition = alltrue([
      for value in values(local.app_secrets) : !strcontains(local.user_data, value)
    ])
    error_message = "User data is readable from the instance and the console; it must not contain secrets."
  }
  assert {
    condition     = strcontains(local.user_data, aws_secretsmanager_secret.app.arn)
    error_message = "User data should fetch secrets from Secrets Manager by ARN."
  }
  assert {
    condition     = strcontains(local.user_data, "nvme-Amazon_Elastic_Block_Store_vol0123456789abcdef0")
    error_message = "User data must mount the data volume by its ID."
  }
  assert {
    condition     = local.fernet_key == "Jc0lAI_zyC6wc_ene_-H9XV6FqB1Nye69IomyBwPXJI="
    error_message = "The encryption key must be 32 bytes of URL-safe base64 (Fernet)."
  }
}

run "instance_role_is_least_privilege" {
  assert {
    condition = alltrue([
      for s in local.instance_policy.Statement : alltrue([for a in s.Action : !strcontains(a, "*")])
    ])
    error_message = "The instance policy must not use wildcard actions."
  }
  assert {
    condition     = one([for s in local.instance_policy.Statement : s.Resource if s.Sid == "ReadAppSecret"]) == [aws_secretsmanager_secret.app.arn]
    error_message = "The instance may read exactly one secret."
  }
  assert {
    condition     = !contains(flatten([for s in local.instance_policy.Statement : s.Action]), "s3:GetObject")
    error_message = "The instance writes backups but must not be able to read them."
  }
}

run "backups_are_private_and_tls_only" {
  assert {
    condition = alltrue([
      aws_s3_bucket_public_access_block.backups.block_public_acls,
      aws_s3_bucket_public_access_block.backups.block_public_policy,
      aws_s3_bucket_public_access_block.backups.ignore_public_acls,
      aws_s3_bucket_public_access_block.backups.restrict_public_buckets,
    ])
    error_message = "The backup bucket must block all public access."
  }
  assert {
    condition     = jsondecode(aws_s3_bucket_policy.backups.policy).Statement[0].Condition.Bool["aws:SecureTransport"] == "false"
    error_message = "The backup bucket must deny plain-HTTP requests."
  }
  assert {
    condition     = one(aws_s3_bucket_server_side_encryption_configuration.backups.rule).apply_server_side_encryption_by_default[0].sse_algorithm == "aws:kms"
    error_message = "Backups must be encrypted with KMS."
  }
}

run "no_deploy_role_without_a_repository" {
  assert {
    condition     = length(aws_iam_role.deploy) == 0 && output.deploy_role_arn == ""
    error_message = "No GitHub deploy role should exist unless github_repository is set."
  }
}

run "deploy_role_is_scoped_to_one_repo_environment_and_instance" {
  variables {
    github_repository = "example/nexusai"
  }
  assert {
    condition     = local.deploy_trust_policy.Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:example/nexusai:environment:production"
    error_message = "Only the production environment of this repository may assume the deploy role."
  }
  assert {
    condition     = contains(local.deploy_policy.Statement[0].Resource, aws_instance.app.arn) && length(local.deploy_policy.Statement[0].Resource) == 2
    error_message = "The deploy role may only run the shell-script document on the app instance."
  }
  assert {
    condition     = length(aws_iam_openid_connect_provider.github) == 1
    error_message = "The GitHub OIDC provider should be created."
  }
}

run "rejects_a_tag_that_is_not_a_commit_sha" {
  command = plan
  variables {
    image_tag = "latest; rm -rf /"
  }
  expect_failures = [var.image_tag]
}

run "rejects_an_invalid_domain" {
  command = plan
  variables {
    domain_name = "https://Not A Domain"
  }
  expect_failures = [var.domain_name]
}
