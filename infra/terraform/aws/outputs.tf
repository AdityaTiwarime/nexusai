output "public_ip" {
  description = "Elastic IP of the server. Point the domain's DNS A record here (done automatically with route53_zone_id)."
  value       = aws_eip.app.public_ip
}

output "url" {
  description = "Where NexusAI is served once DNS points to public_ip."
  value       = "https://${var.domain_name}"
}

output "instance_id" {
  description = "For Session Manager (`aws ssm start-session --target <id>`) and the deploy workflow."
  value       = aws_instance.app.id
}

output "secret_arn" {
  description = "Secrets Manager secret holding the application secrets."
  value       = aws_secretsmanager_secret.app.arn
}

output "backup_bucket" {
  description = "S3 bucket with nightly backups."
  value       = aws_s3_bucket.backups.bucket
}

output "deploy_role_arn" {
  description = "Role for the GitHub Actions deploy workflow (set as the AWS_DEPLOY_ROLE_ARN repository variable). Empty without github_repository."
  value       = local.deploy_enabled ? aws_iam_role.deploy[0].arn : ""
}
