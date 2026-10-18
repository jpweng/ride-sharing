# Only created when var.domain_name is set.
#
# DNS is hosted outside AWS, so validation is manual: apply this resource
# first, add the `acm_validation_records` output at the registrar, then run
# the full apply (which waits here until ACM sees the records).

locals {
  https_enabled = var.domain_name != ""
}

resource "aws_acm_certificate" "app" {
  count = local.https_enabled ? 1 : 0

  domain_name       = var.domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_acm_certificate_validation" "app" {
  count = local.https_enabled ? 1 : 0

  certificate_arn = aws_acm_certificate.app[0].arn

  timeouts {
    create = "2h"
  }
}
