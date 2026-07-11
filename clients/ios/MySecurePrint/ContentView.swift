import SwiftUI
import PrintixSendCore

/// Top-Level-Router.
/// - Nicht eingeloggt → SetupView → LoginView
/// - Eingeloggt → Tabs Upload / Ziele / Konto
struct ContentView: View {

    @StateObject private var settings  = SettingsStore()
    @StateObject private var cache     = AppCache()
    @State private var showSplash = false

    var body: some View {
        Group {
            if settings.isLoggedIn {
                MainTabs()
            } else {
                SetupView()
            }
        }
        .overlay {
            if showSplash {
                MatrixSplashView(style: .splash, onDismiss: { showSplash = false })
                    .environmentObject(cache)
                    .ignoresSafeArea()
            } else if cache.isSyncing {
                MatrixSplashView(style: .overlay, onDismiss: {})
                    .environmentObject(cache)
                    .ignoresSafeArea()
                    .transition(.opacity)
                    .animation(.easeIn(duration: 0.15), value: cache.isSyncing)
            }
        }
        .environmentObject(settings)
        .environmentObject(cache)
        .environmentObject(BackgroundUploadManager.shared)
        // Push-Permission anfordern + Cache befüllen sobald eingeloggt.
        .onChange(of: settings.isLoggedIn) { _, loggedIn in
            if loggedIn {
                showSplash = true
                PushNotificationManager.shared.requestPermissionAndRegister()
                PushNotificationManager.shared.uploadCachedTokenIfNeeded(settings: settings)
                Task { await cache.preloadIfNeeded(settings: settings) }
            } else {
                showSplash = false
                cache.invalidate()
            }
        }
        .onAppear {
            if settings.isLoggedIn && cache.isInitialLoad {
                showSplash = true
            }
        }
        // mysecureprint://setup?server=...&token=...
        // Welcome-Page-QR liefert nur server (Pre-Fill der SetupView).
        // Admin-Mobile-Invite-QR (v0.2.0) liefert zusaetzlich einen
        // Einmal-Token, der spaeter gegen /api/v1/mobile-invite/redeem
        // gegen einen permanenten Bearer-Token getauscht wird.
        .onOpenURL { url in
            handleIncomingURL(url)
        }
    }

    private func handleIncomingURL(_ url: URL) {
        guard url.scheme?.lowercased() == "mysecureprint" else { return }
        let comps = URLComponents(url: url, resolvingAgainstBaseURL: false)
        let items = comps?.queryItems ?? []

        if let server = items.first(where: { $0.name == "server" })?.value,
           !server.isEmpty {
            var s = server.trimmingCharacters(in: .whitespacesAndNewlines)
            while s.hasSuffix("/") { s.removeLast() }
            if !s.lowercased().hasPrefix("http") { s = "https://" + s }
            // v1.6.1: URL-Validierung — verhindert dass ein malicious
            // Deep-Link http://attacker/ einschmuggelt. Nur https oder
            // localhost akzeptieren.
            guard let u = URL(string: s),
                  let host = u.host?.lowercased(),
                  (u.scheme?.lowercased() == "https" ||
                   host == "localhost" || host == "127.0.0.1")
            else {
                return
            }
            // M-1: Server-Wechsel = anderer Tenant/Bearer-Token. Aktive
            // Session sauber beenden, damit der User nicht versehentlich
            // mit altem Token gegen den neuen Server feuert.
            if settings.isLoggedIn && s != settings.serverURL {
                settings.clearSession()
            }
            settings.serverURL = s
        }
        if let token = items.first(where: { $0.name == "token" })?.value,
           !token.isEmpty {
            settings.pendingInviteToken = token
        }
    }
}

// MARK: - Tab metadata (used for ordering + labels)

struct AppTabDef {
    let id: String
    let title: LocalizedStringKey
    let icon: String
}

private let tabDefs: [AppTabDef] = [
    .init(id: "upload",  title: "Upload", icon: "paperplane.fill"),
    .init(id: "targets", title: "Ziele",  icon: "printer.fill"),
    .init(id: "jobs",    title: "Jobs",   icon: "clock.arrow.circlepath"),
]

func tabDef(for id: String) -> AppTabDef {
    tabDefs.first(where: { $0.id == id })
        ?? .init(id: id, title: LocalizedStringKey(id), icon: "questionmark")
}

// MARK: - MainTabs

private struct MainTabs: View {
    @EnvironmentObject private var settings: SettingsStore
    @EnvironmentObject private var cache: AppCache

    var body: some View {
        TabView {
            // ── Sortierbare Haupt-Tabs (Upload / Ziele / Jobs) ──────────
            orderedMainTabs()

            // ── Bedingte Tabs ────────────────────────────────────────────
            // Tab-Limit: iOS bündelt Tabs 6+ automatisch in ein system-
            // generiertes "More"-Menü → doppelte NavigationBar. Daher ≤ 5.
            if settings.hasManagementAccess {
                ManagementView()
                    .tabItem { Label("Management", systemImage: "building.2.fill") }
            } else {
                // v0.8.0 — Normale User (kein Management-Zugriff) bekommen
                // stattdessen prominent AirPrint als eigenen Tab: dort kann
                // er Profile für die Firmen-Queue und ggf. eigene Direkt-
                // Drucker erstellen und aufs Endgerät installieren.
                AirPrintProfilesView()
                    .tabItem { Label(String(localized: "airprint_tab_title"),
                                     systemImage: "printer.filled.and.paper") }
            }

            if settings.hasManagementAccess && settings.hasCardsAccess {
                MoreView()
                    .tabItem { Label(String(localized: "Mehr"), systemImage: "ellipsis") }
            } else {
                if settings.hasCardsAccess {
                    CardsView()
                        .tabItem { Label("Karten", systemImage: "creditcard.fill") }
                }
                AccountView()
                    .tabItem { Label("Konto", systemImage: "person.crop.circle") }
            }
        }
        .tint(MSP.cyan)
        .toolbarBackground(MSP.navy, for: .tabBar)
        .toolbarBackground(.visible, for: .tabBar)
        .toolbarColorScheme(.dark, for: .tabBar)
        // Auf /desktop/me synchronisieren — deckt auch bestehende Sessions
        // ab, fuer die beim urspruenglichen Login der roleType noch nicht
        // gespeichert wurde (App-Update von einer Vorversion).
        //
        // v0.6.5 (iOS): zusaetzlich Targets-Prefetch — vorher musste der
        // User nach Login einmal manuell auf den Ziele-Tab tippen, sonst
        // war selectedTargetIds leer und Upload-Senden disabled. Jetzt
        // wird die Default-Queue ('print:self' mit is_default=true) beim
        // App-Start automatisch in den Store geschrieben.
        .task {
            await refreshRole()
            await cache.preloadIfNeeded(settings: settings)
        }
    }

    // Rendert die drei sortierbaren Tabs in der vom User gespeicherten Reihenfolge.
    @ViewBuilder
    private func orderedMainTabs() -> some View {
        let order = settings.mainTabOrder
        // Muss statisch expandiert werden — ForEach + tabItem verträgt kein
        // dynamisches ViewBuilder-Dispatching über alle SwiftUI-Versionen.
        let t0 = order.indices.contains(0) ? order[0] : "upload"
        let t1 = order.indices.contains(1) ? order[1] : "targets"
        let t2 = order.indices.contains(2) ? order[2] : "jobs"
        singleTab(id: t0)
        singleTab(id: t1)
        singleTab(id: t2)
    }

    @ViewBuilder
    private func singleTab(id: String) -> some View {
        let def = tabDef(for: id)
        switch id {
        case "upload":
            UploadView()
                .tabItem { Label(def.title, systemImage: def.icon) }
        case "targets":
            TargetsView()
                .tabItem { Label(def.title, systemImage: def.icon) }
        default: // "jobs"
            JobsView()
                .tabItem { Label(def.title, systemImage: def.icon) }
        }
    }

    private func refreshRole() async {
        guard let base = settings.serverBaseURL,
              let client = ApiClientFactory.make(baseURL: base.absoluteString,
                                                 token: settings.bearerToken) else {
            return
        }
        do {
            // v1.0.2: meWithFlags statt me() — bringt zusaetzlich
            // delegation_allowed (Admin-Flag) mit, damit iOS den lokalen
            // Delegation-Toggle in Settings ausblenden kann wenn der
            // Admin das Feature server-seitig deaktiviert hat.
            let env = try await client.meWithFlags()
            if let me = env.user {
                settings.userRoleType = me.roleType ?? settings.userRoleType
                if let e = me.email, !e.isEmpty { settings.userEmail = e }
                if let n = me.fullName, !n.isEmpty { settings.userFullName = n }
            }
            settings.delegationAllowedByAdmin = env.delegationAllowed
            settings.employeesCanManageCards = env.employeesCanManageCards
        } catch let ApiError.http(status, _) where status == 401 {
            // M-4: Token serverseitig ungueltig (abgelaufen, widerrufen,
            // User geloescht). Lokale Session leeren, damit die App auf
            // den Setup/Login-Flow zurueckfaellt statt mit totem Token
            // weiterzulaufen.
            settings.clearSession()
        } catch {
            // Silent — Tab bleibt dann eben erstmal versteckt, und beim
            // naechsten Login greift der gespeicherte Wert.
        }
    }
}

/// AccountContent — der eigentliche Konto-Inhalt ohne NavigationStack-Wrapper.
/// Wird sowohl von AccountView (direkter Tab) als auch von MoreView
/// (NavigationLink-Destination) genutzt, damit keine nested NavigationStacks
/// entstehen.
struct AccountContent: View {
    @EnvironmentObject private var settings: SettingsStore
    @EnvironmentObject private var l10n: L10n
    @State private var showTabOrder = false

    var appVersionString: String {
        let info    = Bundle.main.infoDictionary
        let version = info?["CFBundleShortVersionString"] as? String ?? "?"
        let build   = info?["CFBundleVersion"]            as? String ?? "?"
        return "\(version) (\(build))"
    }

    var initials: String {
        let name = settings.userFullName.isEmpty ? settings.userEmail : settings.userFullName
        return name.split(separator: " ")
            .prefix(2)
            .compactMap { $0.first.map(String.init) }
            .joined()
            .uppercased()
    }

    var body: some View {
        List {
                // ── Startseite anpassen ─────────────────────────────────
                Section {
                    Button {
                        showTabOrder = true
                    } label: {
                        HStack {
                            Image(systemName: "square.3.layers.3d")
                                .foregroundColor(MSP.cyan)
                                .frame(width: 24)
                            Text(String(localized: "Tab-Reihenfolge anpassen"))
                            Spacer()
                            HStack(spacing: 4) {
                                ForEach(settings.mainTabOrder, id: \.self) { id in
                                    Image(systemName: tabDef(for: id).icon)
                                        .font(.caption2)
                                        .foregroundColor(.secondary)
                                }
                            }
                            Image(systemName: "chevron.right")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }
                    .buttonStyle(.plain)
                } header: {
                    Text(String(localized: "Startseite"))
                }
                .sheet(isPresented: $showTabOrder) {
                    TabOrderSheet(order: $settings.mainTabOrder)
                }

                // ── Avatar header ───────────────────────────────────────
                Section {
                    HStack(spacing: 16) {
                        ZStack {
                            Circle()
                                .fill(MSP.navyGradient)
                                .frame(width: 56, height: 56)
                            Text(initials.isEmpty ? "?" : initials)
                                .font(.system(size: 20, weight: .bold, design: .rounded))
                                .foregroundColor(.white)
                        }
                        VStack(alignment: .leading, spacing: 3) {
                            if !settings.userFullName.isEmpty {
                                Text(settings.userFullName)
                                    .font(.system(size: 16, weight: .semibold))
                            }
                            if !settings.userEmail.isEmpty {
                                Text(settings.userEmail)
                                    .font(.system(size: 13))
                                    .foregroundColor(.secondary)
                            }
                        }
                    }
                    .padding(.vertical, 6)
                }

                // ── Server ──────────────────────────────────────────────
                Section(String(localized: "Server")) {
                    HStack {
                        Image(systemName: "server.rack")
                            .foregroundColor(MSP.cyan)
                            .frame(width: 24)
                        Text(settings.serverURL)
                            .font(.system(size: 13))
                            .foregroundColor(.secondary)
                            .lineLimit(2)
                    }
                }

                // ── Gerät ───────────────────────────────────────────────
                Section(String(localized: "Gerät")) {
                    HStack {
                        Image(systemName: "iphone")
                            .foregroundColor(MSP.cyan)
                            .frame(width: 24)
                        TextField(String(localized: "Gerätename"), text: $settings.deviceName)
                            .autocorrectionDisabled()
                    }
                }

                // ── Delegation ──────────────────────────────────────────
                Section(String(localized: "Funktionen")) {
                    if settings.delegationAllowedByAdmin {
                        HStack(alignment: .top) {
                            Image(systemName: "person.2.fill")
                                .foregroundColor(MSP.cyan)
                                .frame(width: 24)
                                .padding(.top, 2)
                            Toggle(isOn: $settings.delegateEnabled) {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(String(localized: "Delegation-Druck"))
                                        .font(.system(size: 15))
                                    Text(String(localized: "Senden an andere Printix-Benutzer erlauben."))
                                        .font(.caption)
                                        .foregroundColor(.secondary)
                                }
                            }
                            .tint(MSP.cyan)
                        }
                    } else {
                        HStack {
                            Image(systemName: "lock.fill")
                                .foregroundColor(.secondary)
                                .frame(width: 24)
                            Text(String(localized: "Delegation-Druck vom Admin deaktiviert."))
                                .font(.system(size: 14))
                                .foregroundColor(.secondary)
                        }
                    }
                }

                // ── Auto-Reset ──────────────────────────────────────────
                Section(String(localized: "Auto-Reset Ziel")) {
                    HStack(alignment: .top) {
                        Image(systemName: "clock.arrow.circlepath")
                            .foregroundColor(MSP.cyan)
                            .frame(width: 24)
                            .padding(.top, 2)
                        Toggle(isOn: $settings.autoResetEnabled) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(String(localized: "Auto-Reset aktiv"))
                                    .font(.system(size: 15))
                                Text(String(localized: "Zielauswahl nach Timer auf Secure Print zurücksetzen."))
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                            }
                        }
                        .tint(MSP.cyan)
                    }
                    if settings.autoResetEnabled {
                        HStack {
                            Image(systemName: "timer")
                                .foregroundColor(MSP.cyan)
                                .frame(width: 24)
                            Picker(String(localized: "Timer-Dauer"), selection: $settings.autoResetMinutes) {
                                Text(String(localized: "5 Minuten")).tag(5)
                                Text(String(localized: "10 Minuten")).tag(10)
                                Text(String(localized: "15 Minuten")).tag(15)
                                Text(String(localized: "30 Minuten")).tag(30)
                                Text(String(localized: "60 Minuten")).tag(60)
                            }
                        }
                    }
                }

                // ── Upload ─────────────────────────────────────────────
                Section(String(localized: "Upload")) {
                    HStack(alignment: .top) {
                        Image(systemName: "arrow.up.circle.fill")
                            .foregroundColor(MSP.cyan)
                            .frame(width: 24)
                            .padding(.top, 2)
                        Toggle(isOn: $settings.backgroundUploadEnabled) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(String(localized: "Hintergrund-Senden"))
                                    .font(.system(size: 15))
                                Text(String(localized: "Upload läuft im Hintergrund weiter. Dynamic Island zeigt den Fortschritt."))
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                            }
                        }
                        .tint(MSP.cyan)
                    }
                }

                // ── Druckeinstellungen ──────────────────────────────────
                Section(String(localized: "Druckeinstellungen")) {
                    HStack(alignment: .top) {
                        Image(systemName: "paintpalette.fill")
                            .foregroundColor(MSP.cyan)
                            .frame(width: 24)
                            .padding(.top, 2)
                        Toggle(isOn: $settings.printBW) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(String(localized: "Schwarzweiß drucken"))
                                    .font(.system(size: 15))
                                Text(String(localized: "Bilder werden graustufen-konvertiert. Für Dokumente gilt die Drucker-Einstellung."))
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                            }
                        }
                        .tint(MSP.cyan)
                    }
                    HStack {
                        Image(systemName: "photo.fill")
                            .foregroundColor(MSP.cyan)
                            .frame(width: 24)
                        Picker(String(localized: "Bildgröße"), selection: $settings.printImageSize) {
                            Text(String(localized: "Volle Seite")).tag("full")
                            Text(String(localized: "Foto 10×13 cm")).tag("10x13")
                            Text(String(localized: "Foto 13×18 cm")).tag("13x18")
                            Text(String(localized: "Originalgröße")).tag("original")
                        }
                    }
                }

                // ── Sprache ─────────────────────────────────────────────
                Section(String(localized: "Sprache")) {
                    HStack {
                        Image(systemName: "globe")
                            .foregroundColor(MSP.cyan)
                            .frame(width: 24)
                        Picker(String(localized: "Sprache"), selection: Binding(
                            get: { l10n.pendingLanguage },
                            set: { newLang in
                                l10n.apply(newLang)
                                settings.appLanguage = newLang
                            }
                        )) {
                            ForEach(L10n.supportedLanguages, id: \.code) { lang in
                                Text(verbatim: lang.display).tag(lang.code)
                            }
                        }
                    }
                    if l10n.restartRequired {
                        Label {
                            Text(String(localized: "App neu starten zum Wechseln."))
                                .font(.footnote)
                                .foregroundColor(.secondary)
                        } icon: {
                            Image(systemName: "arrow.clockwise.circle")
                                .foregroundColor(.orange)
                        }
                    }
                }

                // ── Abmelden ────────────────────────────────────────────
                Section {
                    Button(role: .destructive) {
                        settings.clearSession()
                    } label: {
                        HStack {
                            Image(systemName: "rectangle.portrait.and.arrow.right")
                            Text(String(localized: "Abmelden"))
                        }
                    }
                }

                Section {
                    HStack {
                        Image(systemName: "info.circle")
                            .foregroundColor(.secondary)
                            .frame(width: 24)
                        Text(String(localized: "Version"))
                        Spacer()
                        Text(appVersionString)
                            .font(.footnote)
                            .foregroundColor(.secondary)
                    }
                }
            }
            .listStyle(.insetGrouped)
            .brandNavStyle(title: String(localized: "Konto"))
    }
}

private struct AccountView: View {
    var body: some View {
        NavigationStack { AccountContent() }
    }
}

// MARK: - Tab-Reihenfolge Sheet

private struct TabOrderSheet: View {
    @Binding var order: [String]
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section {
                    ForEach(order, id: \.self) { id in
                        let def = tabDef(for: id)
                        HStack(spacing: 14) {
                            Image(systemName: def.icon)
                                .foregroundColor(MSP.cyan)
                                .frame(width: 28)
                            Text(def.title)
                                .font(.system(size: 16))
                            Spacer()
                            Image(systemName: "line.3.horizontal")
                                .foregroundColor(.secondary)
                                .font(.caption)
                        }
                        .padding(.vertical, 4)
                    }
                    .onMove { from, to in
                        order.move(fromOffsets: from, toOffset: to)
                    }
                } header: {
                    Text(String(localized: "Ziehe die Tabs in die gewünschte Reihenfolge"))
                } footer: {
                    Text(String(localized: "Management, Karten und Konto werden immer am Ende angezeigt."))
                }
            }
            .listStyle(.insetGrouped)
            .environment(\.editMode, .constant(.active))
            .navigationTitle(String(localized: "Tab-Reihenfolge"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button(String(localized: "Fertig")) { dismiss() }
                        .foregroundColor(MSP.cyan)
                }
            }
        }
    }
}
