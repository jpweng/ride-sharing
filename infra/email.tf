# Sign-in emails (backend/mailer.py), sent through Amazon SES in this Region
# from <email_from_local_part>@<domain_name>. Only with a domain_name.
#
# - Domain identity with Easy DKIM: three CNAME records in the site's zone.
# - Custom MAIL FROM domain (mail.<domain>): bounces go there, and SPF is
#   checked against it, so both SPF and DKIM align with the From domain for
#   DMARC (dns.tf).
# - The server's instance role may send only from that one address.
#
# SES starts in sandbox mode: it only delivers to verified addresses, at most
# 200 emails a day. Production access is requested once, by a human, in the
# SES console (Account dashboard > Request production access). Until then,
# addresses in ses_sandbox_test_recipients can be used to test sign-in.

locals {
  email_enabled    = local.https_enabled
  email_from       = "${var.email_from_local_part}@${var.domain_name}"
  mail_from_domain = "mail.${var.domain_name}"
}

resource "aws_sesv2_email_identity" "domain" {
  count = local.email_enabled ? 1 : 0

  # Easy DKIM, 2048-bit keys that SES rotates itself.
  email_identity = var.domain_name
}

resource "aws_route53_record" "ses_dkim" {
  count = local.email_enabled ? 3 : 0

  zone_id = aws_route53_zone.site[0].zone_id
  name    = "${aws_sesv2_email_identity.domain[0].dkim_signing_attributes[0].tokens[count.index]}._domainkey.${var.domain_name}"
  type    = "CNAME"
  ttl     = 1800
  records = ["${aws_sesv2_email_identity.domain[0].dkim_signing_attributes[0].tokens[count.index]}.dkim.amazonses.com"]
}

resource "aws_sesv2_email_identity_mail_from_attributes" "domain" {
  count = local.email_enabled ? 1 : 0

  email_identity   = aws_sesv2_email_identity.domain[0].email_identity
  mail_from_domain = local.mail_from_domain
  # If the MX record is ever missing, send with SES's own MAIL FROM rather
  # than not at all (DKIM still aligns, so DMARC still passes).
  behavior_on_mx_failure = "USE_DEFAULT_VALUE"
}

resource "aws_route53_record" "mail_from_mx" {
  count = local.email_enabled ? 1 : 0

  zone_id = aws_route53_zone.site[0].zone_id
  name    = local.mail_from_domain
  type    = "MX"
  ttl     = 3600
  records = ["10 feedback-smtp.${var.aws_region}.amazonses.com"]
}

resource "aws_route53_record" "mail_from_spf" {
  count = local.email_enabled ? 1 : 0

  zone_id = aws_route53_zone.site[0].zone_id
  name    = local.mail_from_domain
  type    = "TXT"
  ttl     = 3600
  records = ["v=spf1 include:amazonses.com -all"]
}

# Sandbox only: recipients to verify (SES emails each a confirmation link),
# so sign-in can be tested before production access is granted.
resource "aws_sesv2_email_identity" "sandbox_recipient" {
  for_each = local.email_enabled ? toset(var.ses_sandbox_test_recipients) : toset([])

  email_identity = each.value
}

# The app container uses the instance role (server.tf, IMDS hop limit 2).
data "aws_iam_policy_document" "send_email" {
  count = local.email_enabled ? 1 : 0

  statement {
    sid     = "SendSignInEmailsFromOneAddress"
    actions = ["ses:SendEmail", "ses:SendRawEmail"]
    # In the sandbox, SES also checks the permission against the (verified)
    # recipient's identity.
    resources = concat(
      [aws_sesv2_email_identity.domain[0].arn],
      [for identity in aws_sesv2_email_identity.sandbox_recipient : identity.arn],
    )

    condition {
      test     = "StringEquals"
      variable = "ses:FromAddress"
      values   = [local.email_from]
    }
  }
}

resource "aws_iam_role_policy" "send_email" {
  count = local.email_enabled ? 1 : 0

  name   = "send-sign-in-emails"
  role   = aws_iam_role.app.id
  policy = data.aws_iam_policy_document.send_email[0].json
}
