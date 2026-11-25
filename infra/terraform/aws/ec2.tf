# Latest Amazon Linux 2023, looked up when the instance is created.
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

# Everything that must survive replacing the server lives on its own encrypted volume:
# Docker's data (PostgreSQL, uploads, certificates), the code checkout and the record
# of the deployed release.
resource "aws_ebs_volume" "data" {
  availability_zone = aws_subnet.public.availability_zone
  size              = var.data_volume_gb
  type              = "gp3"
  encrypted         = true
  kms_key_id        = aws_kms_key.main.arn

  tags = { Name = "${var.project}-data" }
}

locals {
  user_data = templatefile("${path.module}/templates/user_data.sh.tftpl", {
    region              = var.region
    secret_arn          = aws_secretsmanager_secret.app.arn
    data_volume_id      = aws_ebs_volume.data.id
    domain_name         = var.domain_name
    acme_email          = var.acme_email
    image_registry      = var.image_registry
    image_tag           = var.image_tag
    repo_url            = var.repo_url
    git_ref             = var.git_ref
    allow_public_signup = var.allow_public_signup
    backup_bucket       = aws_s3_bucket.backups.bucket
    compose_version     = var.compose_version
    compose_sha256      = var.compose_sha256
  })
}

resource "aws_instance" "app" {
  ami                    = data.aws_ssm_parameter.al2023.insecure_value
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.web.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name
  monitoring             = true
  ebs_optimized          = true

  # Contains no secrets: the instance reads them from Secrets Manager with its role.
  user_data                   = local.user_data
  user_data_replace_on_change = true # safe: data is on aws_ebs_volume.data

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 1          # containers (one hop further) can't reach the metadata service
    instance_metadata_tags      = "disabled"
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_gb
    encrypted             = true
    kms_key_id            = aws_kms_key.main.arn
    delete_on_termination = true
  }

  tags = { Name = var.project }

  lifecycle {
    # A newer AMI shouldn't silently replace the server; patching is a deliberate step.
    ignore_changes = [ami]
  }
}

resource "aws_volume_attachment" "data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.data.id
  instance_id = aws_instance.app.id
}

resource "aws_eip" "app" {
  domain   = "vpc"
  instance = aws_instance.app.id
  tags     = { Name = var.project }
}

resource "aws_route53_record" "app" {
  count   = var.route53_zone_id == "" ? 0 : 1
  zone_id = var.route53_zone_id
  name    = var.domain_name
  type    = "A"
  ttl     = 300
  records = [aws_eip.app.public_ip]
}
