import CryptoKit
import Foundation

/// What the app knows about the site it shows: where it is, which addresses are its own, where
/// each address is opened, and how a sign-in with Google or Apple comes back into the app.
/// Nothing here touches the screen, so RideAndRailTests checks every rule.
enum Site {
    /// The site's host, from Info.plist (`SiteHost`, the build setting SITE_HOST). Always HTTPS.
    static let host: String = {
        let configured = (Bundle.main.object(forInfoDictionaryKey: "SiteHost") as? String ?? "")
            .trimmingCharacters(in: .whitespaces).lowercased()
        return configured.isEmpty ? "rideandrail.ch" : configured
    }()

    static func home(host: String = Site.host) -> URL {
        URL(string: "https://\(host)/")!
    }

    static func isOwn(_ url: URL, host: String = Site.host) -> Bool {
        guard let urlHost = url.host?.lowercased() else { return false }
        return urlHost == host || urlHost == "www.\(host)"
    }

    /// The site's first page, the app itself, whatever its query and fragment say.
    static func isHome(_ url: URL, host: String = Site.host) -> Bool {
        isOwn(url, host: host) && ["", "/", "/index.html"].contains(url.path)
    }

    // MARK: - Where an address is opened

    enum Route: Equatable {
        /// In the app's web view.
        case inApp
        /// In the system's sign-in sheet (see "Sign-in with Google or Apple" below).
        case signIn
        /// Over the app, in a browser sheet or in the app that owns the address (Maps, SBB Mobile).
        case outside
        /// Handed to iOS: an email, a phone number, another app's own kind of address.
        case system
        case refused
    }

    /// Kinds of address a page uses for its own content, never for leaving the app.
    private static let internalSchemes: Set<String> = ["about", "blob", "data"]
    private static let refusedSchemes: Set<String> = ["javascript", "file"]

    /// - Parameters:
    ///   - inMainFrame: the whole page goes there, not a frame inside it.
    ///   - opensNewWindow: a link with `target="_blank"`, or `window.open`.
    ///   - isLinkTap: the person tapped a link (not a redirect, a form or the page's script).
    static func route(for url: URL, inMainFrame: Bool, opensNewWindow: Bool, isLinkTap: Bool,
                      host: String = Site.host) -> Route {
        guard let scheme = url.scheme?.lowercased() else { return .refused }
        if scheme != "https" && scheme != "http" {
            if refusedSchemes.contains(scheme) { return .refused }
            if internalSchemes.contains(scheme) { return opensNewWindow ? .refused : .inApp }
            return inMainFrame || opensNewWindow ? .system : .refused
        }
        if isSignInStart(url, host: host), inMainFrame || opensNewWindow { return .signIn }
        // The site has no tabs here: what it opens in a new one (the code of conduct next to
        // a form being filled in, directions, train tickets) is shown over the app instead.
        if opensNewWindow { return .outside }
        // A frame inside a page: a payment form's, for example.
        if !inMainFrame { return .inApp }
        // Another site the person chose to go to. One the site itself sends them to (the
        // payment page, which sends them back) stays in the app.
        if isLinkTap && !isOwn(url, host: host) { return .outside }
        return .inApp
    }

    // MARK: - Sign-in with Google or Apple
    //
    // Google refuses to sign people in inside an app's web view, and Apple's own sheet (Face ID)
    // only appears in a browser. So the page's "Continue with ..." link, /auth/<provider>/start,
    // is opened in the system's sign-in sheet instead, with `app=ios` and a challenge. There the
    // server, once the provider vouches for the person, doesn't start a session (it would be the
    // sheet's, not the app's): it sends the sheet to `ch.rideandrail.app://signin?code=...`,
    // which closes it and gives the app a code that works once, for a short time, and only with
    // the verifier the challenge was made from. The web view posts both to /auth/app/finish,
    // which starts the session there. See docs/app-sign-in.md.

    /// The address the sign-in sheet ends on. Not registered as the app's URL scheme: only the
    /// sheet the app itself opened hands such an address back.
    static let callbackScheme = "ch.rideandrail.app"
    static let languages: Set<String> = ["en", "de", "fr", "it"]

    static func isSignInStart(_ url: URL, host: String = Site.host) -> Bool {
        guard url.scheme?.lowercased() == "https", isOwn(url, host: host) else { return false }
        let parts = url.path.split(separator: "/", omittingEmptySubsequences: false).map(String.init)
        // "/auth/google/start" -> ["", "auth", "google", "start"]
        return parts.count == 4 && parts[1] == "auth" && parts[3] == "start"
            && !parts[2].isEmpty && parts[2].allSatisfy { $0.isASCII && $0.isLowercase }
    }

    /// A new secret for one sign-in: 32 random bytes, as base64url.
    static func newVerifier() -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        for index in bytes.indices { bytes[index] = UInt8.random(in: .min ... .max) } // SystemRandomNumberGenerator
        return base64url(Data(bytes))
    }

    /// What the server gets when the sign-in starts: the SHA-256 of the verifier, as base64url.
    static func challenge(for verifier: String) -> String {
        base64url(Data(SHA256.hash(data: Data(verifier.utf8))))
    }

    /// The page's sign-in link, as the sign-in sheet opens it.
    static func signInURL(from start: URL, challenge: String) -> URL {
        var components = URLComponents(url: start, resolvingAgainstBaseURL: false)!
        var items = (components.queryItems ?? []).filter { $0.name != "app" && $0.name != "app_challenge" }
        items.append(URLQueryItem(name: "app", value: "ios"))
        items.append(URLQueryItem(name: "app_challenge", value: challenge))
        components.queryItems = items
        return components.url!
    }

    enum SignInAnswer: Equatable {
        case code(String, language: String?)
        /// One of the site's error codes (errors.<code> in its translations).
        case failed(String, language: String?)
    }

    /// What the address the sign-in sheet ended on says; nil if it isn't the server's answer.
    static func signInAnswer(from callback: URL) -> SignInAnswer? {
        guard callback.scheme?.lowercased() == callbackScheme, callback.host == "signin",
              let items = URLComponents(url: callback, resolvingAgainstBaseURL: false)?.queryItems
        else { return nil }
        func value(_ name: String) -> String? { items.first { $0.name == name }?.value }
        let language = value("lang").flatMap { languages.contains($0) ? $0 : nil }
        if let error = value("error") {
            let plain = !error.isEmpty && error.count <= 40 && error.allSatisfy { $0.isASCII && ($0.isLowercase || $0 == "_") }
            return .failed(plain ? error : "social_failed", language: language)
        }
        guard let code = value("code"), (20...200).contains(code.count),
              code.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "-" || $0 == "_") })
        else { return nil }
        return .code(code, language: language)
    }

    /// What the web view loads once the sheet has closed: the code and the verifier, posted to
    /// the server, which starts the session and sends the web view on to the site.
    static func finishRequest(code: String, verifier: String, language: String?, host: String = Site.host) -> URLRequest {
        var request = URLRequest(url: URL(string: "https://\(host)/auth/app/finish")!)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        var form = URLComponents()
        form.queryItems = [URLQueryItem(name: "code", value: code), URLQueryItem(name: "verifier", value: verifier)]
        if let language { form.queryItems?.append(URLQueryItem(name: "lang", value: language)) }
        request.httpBody = Data((form.percentEncodedQuery ?? "").utf8)
        return request
    }

    /// The sign-in page saying why it didn't work, as the site shows it in a browser.
    static func failureURL(error: String, language: String?, host: String = Site.host) -> URL {
        var components = URLComponents(url: home(host: host), resolvingAgainstBaseURL: false)!
        components.queryItems = (language.map { [URLQueryItem(name: "lang", value: $0)] } ?? [])
            + [URLQueryItem(name: "signin_error", value: error)]
        components.fragment = "/ride"
        return components.url!
    }

    private static func base64url(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}
