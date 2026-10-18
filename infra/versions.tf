terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Partial config: the bucket name comes from infra/bootstrap, passed at init:
  #   terraform init -backend-config="bucket=<state_bucket output>"
  backend "s3" {
    key          = "ride-sharing/terraform.tfstate"
    region       = "eu-north-1"
    encrypt      = true
    use_lockfile = true
  }
}
