terraform {
  required_version = ">= 1.9"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
  # State stays local for now (gitignored). Move to an S3 backend before anyone else runs apply.
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "aidolab", ManagedBy = "terraform" }
  }
}

data "aws_caller_identity" "me" {}
data "aws_availability_zones" "available" { state = "available" }

# The aidolab.mn zone was created by hand (2026-10-15) and also holds the mail records,
# so Terraform only reads it and manages its own records inside.
data "aws_route53_zone" "platform" {
  name = var.domain
}
