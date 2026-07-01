import SwiftUI

/// Erster Bildschirm: der User trägt hier nur die Server-URL des
/// MCP-Hosts ein. Alles Weitere (Tenant, Owner, Queue) ergibt sich aus
/// dem Login — der Server kennt ja bereits unseren User.
struct SetupView: View {

    @EnvironmentObject private var settings: SettingsStore

    @State private var draftURL: String = ""
    @State private var showLogin: Bool = false
    @State private var showScanner: Bool = false
    @State private var scanNote: String = ""
    @State private var redeemBusy: Bool = false
    @State private var redeemError: String = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("Server") {
                    TextField("https://mysecureprint.example.com", text: $draftURL)
                        .textInputAutocapitalization(.never)
                        .keyboardType(.URL)
                        .autocorrectionDisabled()

                    Button {
                        scanNote = ""
                        showScanner = true
                    } label: {
                        HStack {
                            Image(systemName: "qrcode.viewfinder")
                            Text("QR scannen")
                            Spacer()
                        }
                    }

                    if !scanNote.isEmpty {
                        Text(scanNote).font(.footnote).foregroundColor(.secondary)
                    }
                }

                if !settings.pendingInviteToken.isEmpty {
                    Section(String(localized: "Einladung")) {
                        Label(String(localized: "Setup-Einladung erkannt — wir versuchen sie automatisch einzulösen."),
                              systemImage: "envelope.badge.fill")
                            .font(.footnote)
                            .foregroundColor(.secondary)
                        if !redeemError.isEmpty {
                            Text(redeemError)
                                .font(.footnote)
                                .foregroundColor(.orange)
                                .textSelection(.enabled)
                        }
                    }
                }

                Section {
                    Button {
                        settings.serverURL = draftURL.trimmingCharacters(in: .whitespacesAndNewlines)
                        Task { await proceed() }
                    } label: {
                        HStack {
                            Spacer()
                            if redeemBusy {
                                ProgressView()
                            } else {
                                Image(systemName: "arrow.right.circle.fill")
                                Text("Weiter zum Login").fontWeight(.semibold)
                            }
                            Spacer()
                        }
                    }
                    .disabled(redeemBusy || !isValidServerURL(draftURL))
                }

                Section {
                    Text(String(localized: "Den QR-Code für die schnelle Einrichtung findest du im Self-Service-Bereich des Management-Portals unter „Mobile App“."))
                        .font(.footnote)
                        .foregroundColor(.secondary)
                }
            }
            .navigationTitle("Setup")
            .onAppear { draftURL = settings.serverURL }
            .navigationDestination(isPresented: $showLogin) {
                LoginView()
            }
            .sheet(isPresented: $showScanner) {
                QRScannerView { value in
                    showScanner = false
                    guard let v = value, !v.isEmpty else {
                        scanNote = String(localized: "Scan abgebrochen oder Kamera nicht verfügbar.")
                        return
                    }
                    // Nur akzeptieren, wenn es nach URL aussieht — sonst
                    // blenden wir den Rohwert als Hinweis ein.
                    if v.lowercased().hasPrefix("http://") || v.lowercased().hasPrefix("https://") {
                        draftURL = v
                        scanNote = String(localized: "Server-URL aus QR übernommen.")
                    } else {
                        scanNote = String(localized: "QR enthält keine gültige Server-URL.")
                    }
                }
                .ignoresSafeArea()
            }
        }
    }

    /// C-3: Wenn ein `pendingInviteToken` vorliegt, versuchen wir zuerst
    /// gegen `/api/v1/mobile-invite/redeem` direkt einen Bearer-Token zu
    /// holen — fertig. Schlaegt das fehl, faellt der Flow auf den
    /// normalen Login zurueck (LoginView). Wenn kein Invite-Token da
    /// ist, springen wir direkt zur LoginView.
    @MainActor
    /// v1.0.1: strenge URL-Validierung (vorher reichte `URL(string: "foo")
    /// != nil` — was wahr ist fuer fast jeden String). Nur http(s) mit
    /// nicht-leerem Host wird akzeptiert; sonst landet der User mit einer
    /// kaputten serverURL bei der Anmeldung.
    private func isValidServerURL(_ raw: String) -> Bool {
        let s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: s),
              let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              let host = url.host, !host.isEmpty,
              host.contains(".") || host == "localhost"
        else { return false }
        return true
    }

    private func proceed() async {
        redeemError = ""
        let invite = settings.pendingInviteToken
        guard !invite.isEmpty else {
            showLogin = true
            return
        }
        redeemBusy = true
        defer { redeemBusy = false }

        do {
            let resp = try await redeemMobileInvite(token: invite)
            // Erfolgreich: Token uebernehmen, Invite verbrauchen,
            // optional User-Daten setzen — ContentView schaltet
            // automatisch auf MainTabs sobald `isLoggedIn` wahr ist.
            settings.bearerToken = resp.bearerToken
            if let u = resp.user {
                if !u.email.isEmpty    { settings.userEmail    = u.email }
                if !u.fullName.isEmpty { settings.userFullName = u.fullName }
                if !u.roleType.isEmpty { settings.userRoleType = u.roleType }
            }
            settings.pendingInviteToken = ""
        } catch {
            // Invite ungueltig/abgelaufen ODER Server verlangt zusaetzlich
            // eine MS-Identitaet (entra_oid). In beiden Faellen ist der
            // Fallback der normale Login-Flow, damit der User sich auf
            // klassischem Weg anmelden kann.
            settings.pendingInviteToken = ""
            redeemError = String(localized: "Einladung ungültig oder abgelaufen.")
            showLogin = true
        }
    }

    private struct RedeemResponse {
        let bearerToken: String
        let user: RedeemUser?
    }
    private struct RedeemUser {
        let email: String
        let fullName: String
        let roleType: String
    }

    /// Roh-POST gegen den Redeem-Endpoint. Server-Antwort:
    /// `{ "bearer_token": "...", "user": { "email", "full_name", "role_type", ... } }`
    /// Server-Endpoint verlangt zusaetzlich `entra_oid` aus einer
    /// verifizierten MS-Identitaet — ohne den schlaegt der Call mit
    /// HTTP 400/`missing_oid` fehl. Dann faellt der Caller auf den
    /// LoginView-Pfad zurueck und der User loggt sich klassisch ein.
    private func redeemMobileInvite(token: String) async throws -> RedeemResponse {
        let base = settings.serverURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let baseURL = URL(string: base) else {
            throw URLError(.badURL)
        }
        let url = baseURL.appendingPathComponent("api/v1/mobile-invite/redeem")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        req.timeoutInterval = 20
        let body: [String: String] = [
            "token":       token,
            "device_name": settings.deviceName,
        ]
        req.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }
        if !(200..<300).contains(http.statusCode) {
            // v1.0.1: nicht alles als "Einladung abgelaufen" verklausuliert —
            // Server-Error-Message extrahieren wenn JSON, sonst Status.
            var msg = "HTTP \(http.statusCode)"
            if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                if let d = obj["error_description"] as? String { msg = d }
                else if let m = obj["message"] as? String { msg = m }
                else if let e = obj["error"] as? String { msg = e }
            }
            throw NSError(domain: "MobileInvite", code: http.statusCode,
                            userInfo: [NSLocalizedDescriptionKey: msg])
        }
        guard let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let bearer = obj["bearer_token"] as? String, !bearer.isEmpty else {
            throw URLError(.cannotParseResponse)
        }
        var user: RedeemUser?
        if let u = obj["user"] as? [String: Any] {
            user = RedeemUser(
                email:    (u["email"]     as? String) ?? "",
                fullName: (u["full_name"] as? String) ?? "",
                roleType: (u["role_type"] as? String) ?? ""
            )
        }
        return RedeemResponse(bearerToken: bearer, user: user)
    }
}
