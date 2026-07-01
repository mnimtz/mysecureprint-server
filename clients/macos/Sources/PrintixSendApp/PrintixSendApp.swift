import SwiftUI
import AppKit
import PrintixSendCore

// Menu-Bar-App-Einstieg. Ziel: so dünn wie der Windows-TrayHost —
// ein NSStatusItem mit Menü: Status, Targets auflisten, Abmelden,
// Konfiguration, Beenden. Keine immer sichtbare Fenster-App.

@main
struct PrintixSendMenuApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    var body: some Scene {
        Settings {
            ConfigView()
                .frame(width: 480, height: 360)
        }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem?
    private let state = AppState.shared

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)  // kein Dock-Icon

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let btn = statusItem?.button {
            btn.title = "🖨"
            btn.toolTip = "Printix Send"
        }
        rebuildMenu()

        Task { await state.bootstrap() }

        // queue: .main stellt sicher, dass wir schon auf dem Main-Thread landen.
        // `MainActor.assumeIsolated` sagt dem Compiler, dass er davon ausgehen
        // kann — so vermeiden wir die "captured var 'self' in concurrently-
        // executing code"-Errors unter Swift 5.10+, die eine Task {…}-Wrapper
        // mit [weak self] nicht mehr akzeptiert.
        NotificationCenter.default.addObserver(forName: .targetsDidChange, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.rebuildMenu() }
        }
        NotificationCenter.default.addObserver(forName: .loginStateDidChange, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.rebuildMenu() }
        }
    }

    @MainActor
    private func rebuildMenu() {
        let menu = NSMenu()
        if let user = state.user {
            let item = NSMenuItem(title: "Angemeldet: \(user.username ?? user.email ?? "-")",
                                  action: nil, keyEquivalent: "")
            item.isEnabled = false
            menu.addItem(item)
        } else {
            menu.addItem(NSMenuItem(title: "Nicht angemeldet", action: nil, keyEquivalent: ""))
        }
        menu.addItem(.separator())

        if !state.targets.isEmpty {
            let header = NSMenuItem(title: "Ziele (Rechtsklick auf Datei → Quick Actions)",
                                    action: nil, keyEquivalent: "")
            header.isEnabled = false
            menu.addItem(header)
            for t in state.targets {
                let m = NSMenuItem(title: "  • \(t.label)", action: nil, keyEquivalent: "")
                m.isEnabled = false
                menu.addItem(m)
            }
            menu.addItem(.separator())
        }

        let resync = NSMenuItem(title: "Quick Actions neu synchronisieren",
                                action: #selector(resyncQuickActions(_:)),
                                keyEquivalent: "r")
        resync.target = self
        menu.addItem(resync)

        if state.user == nil {
            let login = NSMenuItem(title: "Anmelden…", action: #selector(openLogin(_:)), keyEquivalent: "l")
            login.target = self
            menu.addItem(login)
        } else {
            let logout = NSMenuItem(title: "Abmelden", action: #selector(logout(_:)), keyEquivalent: "")
            logout.target = self
            menu.addItem(logout)
        }

        let cfg = NSMenuItem(title: "Konfiguration…", action: #selector(openConfig(_:)), keyEquivalent: ",")
        cfg.target = self
        menu.addItem(cfg)

        menu.addItem(.separator())
        let quit = NSMenuItem(title: "Printix Send beenden", action: #selector(quit(_:)), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)

        statusItem?.menu = menu
    }

    @objc private func resyncQuickActions(_ sender: Any?) {
        Task { await state.refreshTargetsAndSync() }
    }

    @objc private func openLogin(_ sender: Any?) {
        LoginWindowController.shared.show()
    }

    @objc private func logout(_ sender: Any?) {
        Task { await state.logout() }
    }

    @objc private func openConfig(_ sender: Any?) {
        NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc private func quit(_ sender: Any?) {
        NSApp.terminate(nil)
    }
}

extension Notification.Name {
    static let targetsDidChange     = Notification.Name("PrintixSend.targetsDidChange")
    static let loginStateDidChange  = Notification.Name("PrintixSend.loginStateDidChange")
}
