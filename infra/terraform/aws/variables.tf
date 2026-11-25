variable "region" {
  description = "AWS region."
  type        = string
  default     = "ap-south-1"
}

variable "project" {
  description = "Name prefix for every resource."
  type        = string
  default     = "nexusai"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.project))
    error_message = "project must be lowercase letters, digits and dashes (2-21 characters)."
  }
}

variable "domain_name" {
  description = "Public hostname, e.g. nexusai.example.com. Its DNS A record must point to the elastic IP (output `public_ip`) for HTTPS to work."
  type        = string

  validation {
    condition     = can(regex("^([a-z0-9]([a-z0-9-]*[a-z0-9])?\\.)+[a-z]{2,}$", var.domain_name))
    error_message = "domain_name must be a lowercase hostname such as nexusai.example.com."
  }
}

variable "route53_zone_id" {
  description = "Optional Route 53 hosted zone ID. If set, the A record for domain_name is created automatically."
  type        = string
  default     = ""
}

variable "acme_email" {
  description = "Email for Let's Encrypt certificate notices."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.acme_email))
    error_message = "acme_email must be an email address."
  }
}

variable "repo_url" {
  description = "HTTPS URL of the NexusAI git repository (public, or reachable from the instance)."
  type        = string
}

variable "git_ref" {
  description = "Branch the instance checks out at first boot."
  type        = string
  default     = "main"
}

variable "image_registry" {
  description = "Registry prefix of the images built by CI, e.g. ghcr.io/your-github-user."
  type        = string
}

variable "image_tag" {
  description = "Image tag (git commit SHA) deployed at first boot. Later releases are deployed by the GitHub Actions deploy workflow."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{7,40}$", var.image_tag))
    error_message = "image_tag must be a git commit SHA (CI tags images with the commit they were built from)."
  }
}

variable "instance_type" {
  description = "EC2 instance type. t3.small (2 GiB) is the smallest that runs the whole stack comfortably."
  type        = string
  default     = "t3.small"
}

variable "root_volume_gb" {
  description = "Root volume size (operating system only)."
  type        = number
  default     = 20
}

variable "data_volume_gb" {
  description = "Data volume size: Docker images, PostgreSQL, uploaded files and certificates. Survives instance replacement."
  type        = number
  default     = 30
}

variable "vpc_cidr" {
  description = "CIDR block of the VPC."
  type        = string
  default     = "10.20.0.0/16"
}

variable "allow_public_signup" {
  description = "Let anyone create an organization. Off for a private deployment."
  type        = bool
  default     = false
}

variable "backup_retention_days" {
  description = "Days to keep nightly backups in S3."
  type        = number
  default     = 30
}

variable "openai_api_key" {
  description = "Optional OpenAI API key, stored in Secrets Manager. Leave empty to run fully offline."
  type        = string
  default     = ""
  sensitive   = true
}

variable "github_repository" {
  description = "owner/name of the GitHub repository allowed to deploy (via OIDC, no stored AWS keys). Empty = no deploy role."
  type        = string
  default     = ""

  validation {
    condition     = var.github_repository == "" || can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must look like owner/name."
  }
}

variable "create_github_oidc_provider" {
  description = "Create the GitHub OIDC provider. Set false if the AWS account already has one."
  type        = bool
  default     = true
}

variable "compose_version" {
  description = "Docker Compose plugin version installed on the instance."
  type        = string
  default     = "2.29.7"
}

variable "compose_sha256" {
  description = "SHA-256 of docker-compose-linux-x86_64 for compose_version (from the release's .sha256 file)."
  type        = string
  default     = "383ce6698cd5d5bbf958d2c8489ed75094e34a77d340404d9f32c4ae9e12baf0"
}
