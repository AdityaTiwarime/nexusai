# Lets the GitHub Actions deploy workflow act in AWS without stored access keys: GitHub
# issues a short-lived OIDC token, AWS exchanges it for temporary credentials of this
# role. Only workflows of one repository, running in its "production" environment
# (which can require a reviewer), may assume it, and the role can only run commands
# on this one instance.
locals {
  deploy_enabled = var.github_repository != ""
  github_oidc_provider_arn = (
    local.deploy_enabled && var.create_github_oidc_provider
    ? aws_iam_openid_connect_provider.github[0].arn
    : "arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"
  )

  deploy_trust_policy = {
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = local.github_oidc_provider_arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_repository}:environment:production"
        }
      }
    }]
  }

  deploy_policy = {
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RunDeployCommandOnTheAppServerOnly"
        Effect = "Allow"
        Action = ["ssm:SendCommand"]
        Resource = [
          "arn:aws:ssm:${var.region}::document/AWS-RunShellScript",
          aws_instance.app.arn,
        ]
      },
      {
        # These read-only calls don't support resource-level permissions.
        Sid      = "ReadCommandResults"
        Effect   = "Allow"
        Action   = ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations"]
        Resource = ["*"]
      },
    ]
  }
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = local.deploy_enabled && var.create_github_oidc_provider ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # AWS validates GitHub's certificate itself; the list is still required by the API.
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1", "1c58a3a8518e8759bf075b76b750d4f2df264fcd"]
}

resource "aws_iam_role" "deploy" {
  count              = local.deploy_enabled ? 1 : 0
  name_prefix        = "${var.project}-deploy-"
  assume_role_policy = jsonencode(local.deploy_trust_policy)
}

resource "aws_iam_role_policy" "deploy" {
  count  = local.deploy_enabled ? 1 : 0
  name   = "deploy-via-ssm"
  role   = aws_iam_role.deploy[0].id
  policy = jsonencode(local.deploy_policy)
}
