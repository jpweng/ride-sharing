import UIKit

/// The iOS app is the site, https://rideandrail.ch, in a web view (WebViewController). See
/// mobile/ios/README.md.
@main
final class AppDelegate: UIResponder, UIApplicationDelegate {
    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        true
    }
}

final class SceneDelegate: UIResponder, UIWindowSceneDelegate {
    var window: UIWindow?

    /// Left in the background for longer than this, the app loads the site afresh when it
    /// comes back, so that it never keeps running pages from before a deploy for days.
    private static let reloadAfter: TimeInterval = 6 * 60 * 60
    private var leftAt: Date?

    func scene(_ scene: UIScene, willConnectTo session: UISceneSession, options connectionOptions: UIScene.ConnectionOptions) {
        guard let scene = scene as? UIWindowScene else { return }
        let window = UIWindow(windowScene: scene)
        window.overrideUserInterfaceStyle = .light // the site has one look, a light one
        window.tintColor = UIColor(named: "AccentColor")
        window.rootViewController = WebViewController()
        window.makeKeyAndVisible()
        self.window = window
    }

    func sceneDidEnterBackground(_ scene: UIScene) {
        leftAt = Date()
    }

    func sceneWillEnterForeground(_ scene: UIScene) {
        defer { leftAt = nil }
        guard let leftAt, Date().timeIntervalSince(leftAt) > Self.reloadAfter else { return }
        (window?.rootViewController as? WebViewController)?.reload()
    }
}
