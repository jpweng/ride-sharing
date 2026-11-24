import SafariServices
import UIKit
import WebKit

/// The app's one screen: the site in a web view. Site.route says where each address is opened;
/// this class does what it says, and stands in for what a browser provides around a page:
/// dialogs (alert, confirm), a place for links that open a new tab, a "no connection" screen.
final class WebViewController: UIViewController {
    private lazy var webView: WKWebView = {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default() // cookies kept between launches: stays signed in
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1"
        // The end of the User-Agent, where a browser has its own name.
        configuration.applicationNameForUserAgent = "Mobile/15E148 RideAndRailApp/\(version)"
        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = true
        webView.isOpaque = false // the view's own background shows until the first page is there
        webView.backgroundColor = .clear
        return webView
    }()

    private let spinner = UIActivityIndicatorView(style: .large)
    private let noConnection = NoConnectionView()
    private let signIn = SignIn()

    override var preferredStatusBarStyle: UIStatusBarStyle { .darkContent }

    override func viewDidLoad() {
        super.viewDidLoad()
        // Behind the status bar: the white of the site's header, right below it.
        view.backgroundColor = UIColor(named: "LaunchBackground")
        for subview in [webView, spinner, noConnection] {
            subview.translatesAutoresizingMaskIntoConstraints = false
            view.addSubview(subview)
        }
        let safe = view.safeAreaLayoutGuide
        NSLayoutConstraint.activate([
            // The site doesn't draw under the status bar; at the bottom it scrolls under the
            // home indicator as in Safari.
            webView.topAnchor.constraint(equalTo: safe.topAnchor),
            webView.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            webView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            spinner.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            spinner.centerYAnchor.constraint(equalTo: view.centerYAnchor),
            noConnection.topAnchor.constraint(equalTo: view.topAnchor),
            noConnection.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            noConnection.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            noConnection.trailingAnchor.constraint(equalTo: view.trailingAnchor),
        ])
        noConnection.isHidden = true
        noConnection.retry = { [weak self] in self?.loadHome() }
        loadHome()
    }

    private func loadHome() {
        noConnection.isHidden = true
        spinner.startAnimating()
        webView.load(URLRequest(url: Site.home()))
    }

    /// The page again, as it is on the server now.
    func reload() {
        if webView.url == nil { loadHome() } else { webView.reload() }
    }

    private func showOutside(_ url: URL) {
        let showBrowser = { [weak self] in
            let browser = SFSafariViewController(url: url)
            browser.preferredControlTintColor = UIColor(named: "AccentColor")
            self?.present(browser, animated: true)
        }
        // The site's own pages always in the browser sheet; another site's in its app when
        // that is installed (directions in Maps, tickets in SBB Mobile).
        if Site.isOwn(url) { return showBrowser() }
        UIApplication.shared.open(url, options: [.universalLinksOnly: true]) { opened in
            if !opened { showBrowser() }
        }
    }

    private func failed(_ error: Error) {
        let error = error as NSError
        // Not failures: a load replaced by the next one, or one this class sent elsewhere.
        if error.domain == NSURLErrorDomain && error.code == NSURLErrorCancelled { return }
        if error.domain == "WebKitErrorDomain" && error.code == 102 { return }
        spinner.stopAnimating()
        if webView.url == nil {
            // Nothing to show yet: the whole screen says so, with a button to try again.
            noConnection.isHidden = false
        } else if presentedViewController == nil {
            // A page is showing, and stays: the next one couldn't be loaded.
            let alert = UIAlertController(title: Text.noConnectionTitle, message: Text.noConnectionMessage, preferredStyle: .alert)
            alert.addAction(UIAlertAction(title: Text.ok, style: .default))
            present(alert, animated: true)
        }
    }
}

extension WebViewController: WKNavigationDelegate {
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { return decisionHandler(.cancel) }
        let route = Site.route(
            for: url,
            inMainFrame: action.targetFrame?.isMainFrame ?? false,
            opensNewWindow: action.targetFrame == nil,
            isLinkTap: action.navigationType == .linkActivated
        )
        switch route {
        case .inApp:
            return decisionHandler(.allow)
        case .signIn:
            signIn.begin(url, over: view.window) { [weak self] next in
                if let next { self?.webView.load(next) }
            }
        case .outside:
            showOutside(url)
        case .system:
            UIApplication.shared.open(url)
        case .refused:
            break
        }
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        spinner.stopAnimating()
        noConnection.isHidden = true
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        failed(error)
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        // iOS stopped the page to free memory while the app was in the background.
        reload()
    }
}

extension WebViewController: WKUIDelegate {
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        nil // new windows never get this far: decidePolicyFor shows them over the app
    }

    // A web view shows none of a page's dialogs by itself. The site asks before cancelling a
    // drive or deleting the account (confirm), and reports some failures with alert.

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        guard presentedViewController == nil else { return completionHandler() }
        let alert = UIAlertController(title: nil, message: message, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: Text.ok, style: .default) { _ in completionHandler() })
        present(alert, animated: true)
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        guard presentedViewController == nil else { return completionHandler(false) }
        let alert = UIAlertController(title: nil, message: message, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: Text.cancel, style: .cancel) { _ in completionHandler(false) })
        alert.addAction(UIAlertAction(title: Text.ok, style: .default) { _ in completionHandler(true) })
        present(alert, animated: true)
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        guard presentedViewController == nil else { return completionHandler(nil) }
        let alert = UIAlertController(title: nil, message: prompt, preferredStyle: .alert)
        alert.addTextField { $0.text = defaultText }
        alert.addAction(UIAlertAction(title: Text.cancel, style: .cancel) { _ in completionHandler(nil) })
        alert.addAction(UIAlertAction(title: Text.ok, style: .default) { [weak alert] _ in
            completionHandler(alert?.textFields?.first?.text ?? "")
        })
        present(alert, animated: true)
    }
}

/// The app's own few texts (Localizable.strings, in the site's four languages).
enum Text {
    static let ok = NSLocalizedString("dialog.ok", comment: "")
    static let cancel = NSLocalizedString("dialog.cancel", comment: "")
    static let noConnectionTitle = NSLocalizedString("offline.title", comment: "")
    static let noConnectionMessage = NSLocalizedString("offline.message", comment: "")
    static let tryAgain = NSLocalizedString("offline.retry", comment: "")
}

/// Shown instead of the site when its first page can't be loaded.
final class NoConnectionView: UIView {
    var retry: (() -> Void)?

    override init(frame: CGRect) {
        super.init(frame: frame)
        backgroundColor = UIColor(named: "LaunchBackground")

        let title = UILabel()
        title.text = Text.noConnectionTitle
        title.font = .preferredFont(forTextStyle: .title2)
        let message = UILabel()
        message.text = Text.noConnectionMessage
        message.font = .preferredFont(forTextStyle: .body)
        message.textColor = .secondaryLabel
        for label in [title, message] {
            label.numberOfLines = 0
            label.textAlignment = .center
            label.adjustsFontForContentSizeCategory = true
        }
        var filled = UIButton.Configuration.filled()
        filled.title = Text.tryAgain
        filled.buttonSize = .large
        let button = UIButton(configuration: filled, primaryAction: UIAction { [weak self] _ in self?.retry?() })

        let stack = UIStackView(arrangedSubviews: [title, message, button])
        stack.axis = .vertical
        stack.alignment = .center
        stack.spacing = 12
        stack.setCustomSpacing(24, after: message)
        stack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerYAnchor.constraint(equalTo: centerYAnchor),
            stack.leadingAnchor.constraint(equalTo: layoutMarginsGuide.leadingAnchor, constant: 16),
            stack.trailingAnchor.constraint(equalTo: layoutMarginsGuide.trailingAnchor, constant: -16),
        ])
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("not used: the view is made in code")
    }
}
