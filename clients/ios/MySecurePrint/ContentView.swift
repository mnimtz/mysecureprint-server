import SwiftUI
import PrintixSendCore

/// Top-Level-Router.
/// - Nicht eingeloggt → SetupView → LoginView
/// - Eingeloggt → Tabs Upload / Ziele / Konto
struct ContentView: View {

    @StateObject private var settings = SettingsStore()
    @StateObject private var cache    = AppCache()

    var body: some View {
        Group {
            if settings.isLoggedIn {
                MainTabs()
            } else {
                SetupView()
            }
        }
        .environmentObject(settings)
        .environmentObject(cache)
        // Push-Permission anfordern + Cache befüllen sobald eingeloggt.
        .onChange(of: settings.isLoggedIn) { _, loggedIn in
            if loggedIn {
                PushNotificationManager.shared.requestPermissionAndRegister()
                PushNotificationManager.shared.uploadCachedTokenIfNeeded(settings: settings)
                Task { await cache.preloadIfNeeded(settings: settings) }
            } else {
                cache.invalidate()
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

private struct MainTabs: View {
    @EnvironmentObject private var settings: SettingsStore
    @EnvironmentObject private var cache: AppCache

    var body: some View {
        TabView {
            UploadView()
                .tabItem { Label("Upload", systemImage: "paperplane.fill") }

            TargetsView()
                .tabItem { Label("Ziele", systemImage: "printer.fill") }

            JobsView()
                .tabItem { Label("Jobs", systemImage: "clock.arrow.circlepath") }

            if settings.hasManagementAccess {
                ManagementView()
                    .tabItem { Label("Management", systemImage: "building.2.fill") }
            }

            // Tab-Limit: iOS bündelt Tabs 6+ automatisch in ein system-
            // generiertes "More"-Menü, das die eigenen NavigationStacks
            // der Views in einen weiteren NavigationStack pusht →
            // doppelte/überhöhte Navigationsleiste. Daher immer ≤ 5 Tabs:
            //
            // management + cards  → 5. Tab = eigenes MoreView (Cards + Konto)
            // management + !cards → 5. Tab = Konto direkt
            // !management + cards → 4+5. Tab = Cards + Konto direkt
            // !management + !cards→ 4. Tab = Konto direkt
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
