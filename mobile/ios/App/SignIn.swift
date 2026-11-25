import AuthenticationServices
import UIKit

/// Sign-in with Google or Apple in the system's sign-in sheet (ASWebAuthenticationSession: a
/// browser over the app, with the person's own Safari sessions). Site.swift explains why, and
/// how the answer gets back into the web view.
final class SignIn: NSObject, ASWebAuthenticationPresentationContextProviding {
    private var session: ASWebAuthenticationSession?
    private weak var window: UIWindow?

    /// Opens the sheet on the page's sign-in link `start`. `done` gets what the web view loads
    /// next, or nil when the person closed the sheet (the sign-in page is still there).
    func begin(_ start: URL, over window: UIWindow?, done: @escaping (URLRequest?) -> Void) {
        session?.cancel()
        self.window = window
        let verifier = Site.newVerifier()
        let url = Site.signInURL(from: start, challenge: Site.challenge(for: verifier))
        let session = ASWebAuthenticationSession(url: url, callbackURLScheme: Site.callbackScheme) { [weak self] callback, _ in
            DispatchQueue.main.async {
                self?.session = nil
                // No callback: the sheet was closed, or couldn't be shown.
                guard let callback, let answer = Site.signInAnswer(from: callback) else { return done(nil) }
                switch answer {
                case let .code(code, language):
                    done(Site.finishRequest(code: code, verifier: verifier, language: language))
                case let .failed(error, language):
                    done(URLRequest(url: Site.failureURL(error: error, language: language)))
                }
            }
        }
        session.presentationContextProvider = self
        // Not a private session: whoever is signed in to Google in Safari is offered here.
        session.prefersEphemeralWebBrowserSession = false
        self.session = session
        if !session.start() {
            self.session = nil
            done(nil)
        }
    }

    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        window ?? ASPresentationAnchor()
    }
}
