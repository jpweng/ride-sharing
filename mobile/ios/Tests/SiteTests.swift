import XCTest
@testable import RideAndRail

/// The rules of Site.swift: where each address is opened, and the sign-in hand-off.
final class SiteTests: XCTestCase {
    private let host = "rideandrail.ch"

    private func route(_ address: String, inMainFrame: Bool = true, opensNewWindow: Bool = false,
                       isLinkTap: Bool = false) -> Site.Route {
        Site.route(for: URL(string: address)!, inMainFrame: inMainFrame, opensNewWindow: opensNewWindow,
                   isLinkTap: isLinkTap, host: host)
    }

    // MARK: - Where an address is opened

    func testTheSitesOwnPagesStayInTheApp() {
        XCTAssertEqual(route("https://rideandrail.ch/"), .inApp)
        XCTAssertEqual(route("https://rideandrail.ch/about.html", isLinkTap: true), .inApp)
        XCTAssertEqual(route("https://www.rideandrail.ch/privacy.html", isLinkTap: true), .inApp)
        XCTAssertEqual(route("https://RideAndRail.ch/?lang=de#/ride"), .inApp)
    }

    func testTheSitesFirstPage() {
        XCTAssertTrue(Site.isHome(URL(string: "https://rideandrail.ch")!, host: host))
        XCTAssertTrue(Site.isHome(URL(string: "https://rideandrail.ch/?lang=de#/ride")!, host: host))
        XCTAssertTrue(Site.isHome(URL(string: "https://www.rideandrail.ch/index.html")!, host: host))
        XCTAssertFalse(Site.isHome(URL(string: "https://rideandrail.ch/conduct.html")!, host: host))
        XCTAssertFalse(Site.isHome(URL(string: "https://example.com/")!, host: host))
    }

    func testALinkToAnotherSiteIsShownOverTheApp() {
        XCTAssertEqual(route("https://www.sbb.ch/en", isLinkTap: true), .outside)
        // A look-alike host is another site.
        XCTAssertEqual(route("https://rideandrail.ch.example.com/", isLinkTap: true), .outside)
        XCTAssertEqual(route("https://evilrideandrail.ch/", isLinkTap: true), .outside)
    }

    func testWhereTheSiteItselfSendsThePersonStaysInTheApp() {
        // The payment page: the site's script goes there, and it sends the person back.
        XCTAssertEqual(route("https://checkout.stripe.com/c/pay/cs_test_1"), .inApp)
    }

    func testANewTabIsShownOverTheApp() {
        XCTAssertEqual(route("https://rideandrail.ch/conduct.html", inMainFrame: false, opensNewWindow: true, isLinkTap: true), .outside)
        XCTAssertEqual(route("https://www.google.com/maps/dir/?api=1", inMainFrame: false, opensNewWindow: true, isLinkTap: true), .outside)
    }

    func testAFrameInsideAPageLoadsWhereItIs() {
        XCTAssertEqual(route("https://js.stripe.com/v3/frame.html", inMainFrame: false), .inApp)
        XCTAssertEqual(route("about:blank", inMainFrame: false), .inApp)
        XCTAssertEqual(route("twint://pay", inMainFrame: false), .refused)
    }

    func testOtherKindsOfAddress() {
        XCTAssertEqual(route("mailto:someone@example.com", isLinkTap: true), .system)
        XCTAssertEqual(route("tel:+41000000000", isLinkTap: true), .system)
        XCTAssertEqual(route("about:blank"), .inApp)
        XCTAssertEqual(route("javascript:alert(1)", isLinkTap: true), .refused)
        XCTAssertEqual(route("file:///etc/hosts"), .refused)
        XCTAssertEqual(route("data:text/html,hello", inMainFrame: false, opensNewWindow: true), .refused)
    }

    // MARK: - Sign-in with Google or Apple

    func testTheSignInLinksGoToTheSignInSheet() {
        XCTAssertEqual(route("https://rideandrail.ch/auth/google/start?lang=fr", isLinkTap: true), .signIn)
        XCTAssertEqual(route("https://rideandrail.ch/auth/apple/start?lang=de", isLinkTap: true), .signIn)
        // Not the way back from the provider, and not another site's address of the same shape.
        XCTAssertEqual(route("https://rideandrail.ch/auth/google/callback?code=1"), .inApp)
        XCTAssertEqual(route("https://rideandrail.ch/auth//start"), .inApp)
        XCTAssertEqual(route("https://rideandrail.ch/auth/google/start/more"), .inApp)
        XCTAssertEqual(route("https://example.com/auth/google/start", isLinkTap: true), .outside)
        XCTAssertEqual(route("http://rideandrail.ch/auth/google/start"), .inApp)
    }

    func testTheSheetOpensTheLinkWithTheAppAndItsChallenge() {
        let url = Site.signInURL(from: URL(string: "https://rideandrail.ch/auth/google/start?lang=it&app=other")!, challenge: "abc-_")
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)!.queryItems!
        XCTAssertEqual(url.path, "/auth/google/start")
        XCTAssertEqual(items.map(\.name).sorted(), ["app", "app_challenge", "lang"])
        XCTAssertEqual(items.first { $0.name == "lang" }?.value, "it")
        XCTAssertEqual(items.first { $0.name == "app" }?.value, "ios")
        XCTAssertEqual(items.first { $0.name == "app_challenge" }?.value, "abc-_")
    }

    func testTheChallengeIsTheVerifiersSHA256AsBase64URL() {
        // The example of RFC 7636, appendix B.
        XCTAssertEqual(Site.challenge(for: "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
                       "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")
    }

    func testEverySignInHasItsOwnVerifier() {
        let first = Site.newVerifier(), second = Site.newVerifier()
        XCTAssertNotEqual(first, second)
        XCTAssertEqual(first.count, 43) // 32 bytes as base64url, no padding
        XCTAssertNil(first.rangeOfCharacter(from: CharacterSet(charactersIn: "+/=")))
    }

    func testTheServersAnswer() {
        let code = String(repeating: "aB3-_", count: 8)
        XCTAssertEqual(Site.signInAnswer(from: URL(string: "ch.rideandrail.app://signin?code=\(code)&lang=de")!),
                       .code(code, language: "de"))
        XCTAssertEqual(Site.signInAnswer(from: URL(string: "ch.rideandrail.app://signin?code=\(code)&lang=xx")!),
                       .code(code, language: nil))
        XCTAssertEqual(Site.signInAnswer(from: URL(string: "ch.rideandrail.app://signin?error=social_cancelled&lang=fr")!),
                       .failed("social_cancelled", language: "fr"))
        // Only the site's own error codes are passed on.
        XCTAssertEqual(Site.signInAnswer(from: URL(string: "ch.rideandrail.app://signin?error=%3Cb%3E")!),
                       .failed("social_failed", language: nil))
    }

    func testWhatIsNotTheServersAnswer() {
        let code = String(repeating: "a", count: 40)
        XCTAssertNil(Site.signInAnswer(from: URL(string: "https://rideandrail.ch/signin?code=\(code)")!))
        XCTAssertNil(Site.signInAnswer(from: URL(string: "ch.rideandrail.app://other?code=\(code)")!))
        XCTAssertNil(Site.signInAnswer(from: URL(string: "ch.rideandrail.app://signin")!))
        XCTAssertNil(Site.signInAnswer(from: URL(string: "ch.rideandrail.app://signin?code=short")!))
        XCTAssertNil(Site.signInAnswer(from: URL(string: "ch.rideandrail.app://signin?code=\(code)%26x%3D1")!))
    }

    func testTheCodeAndTheVerifierArePostedToTheSite() {
        let request = Site.finishRequest(code: "the-code", verifier: "the_verifier", language: "fr", host: host)
        XCTAssertEqual(request.url?.absoluteString, "https://rideandrail.ch/auth/app/finish")
        XCTAssertEqual(request.httpMethod, "POST")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/x-www-form-urlencoded")
        XCTAssertEqual(String(data: request.httpBody ?? Data(), encoding: .utf8), "code=the-code&verifier=the_verifier&lang=fr")
        let plain = Site.finishRequest(code: "c", verifier: "v", language: nil, host: host)
        XCTAssertEqual(String(data: plain.httpBody ?? Data(), encoding: .utf8), "code=c&verifier=v")
    }

    func testAFailedSignInIsShownOnTheSitesSignInPage() {
        XCTAssertEqual(Site.failureURL(error: "social_failed", language: "de", host: host).absoluteString,
                       "https://rideandrail.ch/?lang=de&signin_error=social_failed#/ride")
        XCTAssertEqual(Site.failureURL(error: "account_banned", language: nil, host: host).absoluteString,
                       "https://rideandrail.ch/?signin_error=account_banned#/ride")
    }
}
