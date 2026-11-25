data "aws_caller_identity" "current" {}

# One customer-managed key for the secret, the disks and the backups, rotated yearly.
resource "aws_kms_key" "main" {
  description             = "${var.project}: secrets, EBS volumes and backups"
  enable_key_rotation     = true
  deletion_window_in_days = 7

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Standard default: the account (its IAM policies) decides who may use the key.
        Sid       = "AccountAdministration"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        # EC2 Auto Recovery and EBS need grants to use the key for encrypted volumes.
        Sid       = "AllowEbsThroughEc2"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = ["kms:CreateGrant", "kms:Decrypt", "kms:DescribeKey", "kms:GenerateDataKeyWithoutPlainText", "kms:ReEncrypt*"]
        Resource  = "*"
        Condition = {
          StringEquals = { "kms:ViaService" = "ec2.${var.region}.amazonaws.com" }
        }
      },
    ]
  })
}

resource "aws_kms_alias" "main" {
  name          = "alias/${var.project}"
  target_key_id = aws_kms_key.main.key_id
}

# Generated here, never typed by a person, never in user data or git.
resource "random_password" "postgres" {
  length  = 40
  special = false
}

resource "random_password" "redis" {
  length  = 40
  special = false
}

resource "random_password" "jwt" {
  length  = 64
  special = false
}

resource "random_bytes" "encryption_key" {
  length = 32
}

locals {
  # A Fernet key is 32 random bytes in URL-safe base64.
  fernet_key = replace(replace(random_bytes.encryption_key.base64, "+", "-"), "/", "_")

  app_secrets = merge(
    {
      POSTGRES_PASSWORD    = random_password.postgres.result
      REDIS_PASSWORD       = random_password.redis.result
      NEXUS_JWT_SECRET     = random_password.jwt.result
      NEXUS_ENCRYPTION_KEY = local.fernet_key
    },
    var.openai_api_key == "" ? {} : { NEXUS_OPENAI_API_KEY = var.openai_api_key },
  )
}

resource "aws_secretsmanager_secret" "app" {
  #checkov:skip=CKV2_AWS_57:Automatic rotation needs app support (re-encrypting stored credentials, re-issuing sessions); rotation is a documented manual procedure.
  name_prefix             = "${var.project}/app-"
  description             = "NexusAI application secrets (database, Redis, JWT, encryption key)"
  kms_key_id              = aws_kms_key.main.arn
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id     = aws_secretsmanager_secret.app.id
  secret_string = jsonencode(local.app_secrets)
}
