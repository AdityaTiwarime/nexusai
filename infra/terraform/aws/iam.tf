# What the server itself may do. Least privilege: read its one secret, write (not read)
# backups, and register with Systems Manager. Restoring a backup is done by an
# administrator, so a compromised server can't read old backups.
resource "aws_iam_role" "instance" {
  name_prefix = "${var.project}-instance-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# Session Manager (shell access without SSH) and Run Command (deployments).
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

locals {
  instance_policy = {
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadAppSecret"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [aws_secretsmanager_secret.app.arn]
      },
      {
        Sid      = "WriteBackups"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:AbortMultipartUpload", "s3:DeleteObject"]
        Resource = ["${aws_s3_bucket.backups.arn}/db/*", "${aws_s3_bucket.backups.arn}/uploads/*"]
      },
      {
        Sid      = "UseKeyForSecretAndBackupsOnly"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource = [aws_kms_key.main.arn]
        Condition = {
          StringEquals = {
            "kms:ViaService" = [
              "secretsmanager.${var.region}.amazonaws.com",
              "s3.${var.region}.amazonaws.com",
            ]
          }
        }
      },
    ]
  }
}

resource "aws_iam_role_policy" "instance" {
  name   = "nexusai-app"
  role   = aws_iam_role.instance.id
  policy = jsonencode(local.instance_policy)
}

resource "aws_iam_instance_profile" "instance" {
  name_prefix = "${var.project}-"
  role        = aws_iam_role.instance.name
}
