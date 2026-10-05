import Capacitor
import UIKit

// Registers the app-local BackgroundSync plugin here because `npx cap sync` rewrites
// packageClassList and would drop it; capacitorDidLoad() runs before the webview loads JS.
@objc(MainViewController)
class MainViewController: CAPBridgeViewController {
    override open func capacitorDidLoad() {
        bridge?.registerPluginInstance(BackgroundSyncPlugin())
    }
}
