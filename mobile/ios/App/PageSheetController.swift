import SafariServices
import UIKit
import WebKit

/// One of the site's own pages that the site opens in a new tab, shown over the app's screen
/// with a "Done" button: the page under it stays as it was. A web view like the main one
/// (WebViewController.configuration), not a browser sheet, so the page is the site as the app
/// shows it.
final class PageSheetController: UIViewController, WKNavigationDelegate {
    private let url: URL
    private lazy var webView = WKWebView(frame: .zero, configuration: WebViewController.configuration())

    init(url: URL) {
        self.url = url
        super.init(nibName: nil, bundle: nil)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("not used: the view is made in code")
    }

    override func loadView() {
        view = webView
    }

    override func viewDidLoad() {
        super.viewDidLoad()
        webView.navigationDelegate = self
        navigationItem.rightBarButtonItem = UIBarButtonItem(
            systemItem: .done, primaryAction: UIAction { [weak self] _ in self?.dismiss(animated: true) }
        )
        webView.load(URLRequest(url: url))
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let target = action.request.url else { return decisionHandler(.cancel) }
        let inMainFrame = action.targetFrame?.isMainFrame ?? false
        let route = Site.route(for: target, inMainFrame: inMainFrame, opensNewWindow: action.targetFrame == nil,
                               isLinkTap: action.navigationType == .linkActivated)
        switch route {
        case .inApp:
            // "Back to the app": the app is under this sheet.
            if inMainFrame, Site.isHome(target) {
                dismiss(animated: true)
                return decisionHandler(.cancel)
            }
            return decisionHandler(.allow)
        case .outside where Site.isOwn(target):
            webView.load(URLRequest(url: target)) // a new tab from here: this sheet
        case .outside:
            UIApplication.shared.open(target, options: [.universalLinksOnly: true]) { [weak self] opened in
                if !opened { self?.present(SFSafariViewController(url: target), animated: true) }
            }
        case .system:
            UIApplication.shared.open(target)
        case .signIn, .refused:
            break
        }
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        title = webView.title
    }
}
