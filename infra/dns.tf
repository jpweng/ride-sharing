# DNS for domain_name. The domain is registered elsewhere (rideandrail.ch at
# Hostpoint); its nameservers there must be set to this zone's name_servers
# output.

resource "aws_route53_zone" "site" {
  count = local.https_enabled ? 1 : 0

  name = var.domain_name
}

# Apex and www both point at the server; Caddy redirects www to the apex.
resource "aws_route53_record" "site" {
  for_each = local.https_enabled ? toset([var.domain_name, "www.${var.domain_name}"]) : toset([])

  zone_id = aws_route53_zone.site[0].zone_id
  name    = each.value
  type    = "A"
  ttl     = 300
  records = [aws_eip.app.public_ip]
}

# The app sends no mail: publish that nothing may send as the domain, so it
# can't be spoofed.
resource "aws_route53_record" "no_mail_spf" {
  count = local.https_enabled ? 1 : 0

  zone_id = aws_route53_zone.site[0].zone_id
  name    = var.domain_name
  type    = "TXT"
  ttl     = 3600
  records = ["v=spf1 -all"]
}

resource "aws_route53_record" "no_mail_dmarc" {
  count = local.https_enabled ? 1 : 0

  zone_id = aws_route53_zone.site[0].zone_id
  name    = "_dmarc.${var.domain_name}"
  type    = "TXT"
  ttl     = 3600
  records = ["v=DMARC1; p=reject"]
}
