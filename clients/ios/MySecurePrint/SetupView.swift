import SwiftUI

struct SetupView: View {

    @EnvironmentObject private var settings: SettingsStore

    @State private var draftURL:    String = ""
    @State private var showLogin:   Bool   = false
    @State private var showScanner: Bool   = false
    @State private var scanNote:    String = ""
    @State private var redeemBusy:  Bool   = false
    @State private var redeemError: String = ""

    var body: some View {
        NavigationStack {
            ZStack {
                MSP.navyGradient.ignoresSafeArea()

                ScrollView(showsIndicators: false) {
                    VStack(spacing: 0) {

                        // ── Logo ────────────────────────────────────────────
                        BrandHeader(subtitle: String(localized: "Server einrichten"))
                            .padding(.top, 64)
                            .padding(.bottom, 44)

                        // ── Server-URL card ─────────────────────────────────
                        VStack(alignment: .leading, spacing: 8) {
                            Text(String(localized: "Server-URL"))
                                .font(.system(size: 12, weight: .semibold))
                                .foregroundColor(.white.opacity(0.5))
                                .textCase(.uppercase)
                                .tracking(0.8)

                            BrandTextField(
                                label: "https://mysecureprint.example.com",
                                icon: "server.rack",
                                text: $draftURL,
                                keyboardType: .URL
                            )

                            // QR scan button
                            Button {
                                scanNote = ""
                                showScanner = true
                            } label: {
                                HStack(spacing: 10) {
                                    Image(systemName: "qrcode.viewfinder")
                                        .font(.system(size: 18))
                                        .foregroundColor(MSP.cyan)
                                    Text(String(localized: "QR-Code scannen"))
                                        .font(.system(size: 15, weight: .medium))
                                        .foregroundColor(.white.opacity(0.85))
                                    Spacer()
                                    Image(systemName: "chevron.right")
                                        .font(.system(size: 12, weight: .semibold))
                                        .foregroundColor(.white.opacity(0.3))
                                }
                                .padding(.horizontal, 16)
                                .padding(.vertical, 14)
                                .background(MSP.glass)
                                .cornerRadius(12)
                                .overlay(
                                    RoundedRectangle(cornerRadius: 12)
                                        .stroke(MSP.glassBorder, lineWidth: 1)
                                )
                            }

                            if !scanNote.isEmpty {
                                HStack(spacing: 6) {
                                    Image(systemName: "checkmark.circle.fill")
                                        .foregroundColor(MSP.green)
                                        .font(.system(size: 13))
                                    Text(scanNote)
                                        .font(.system(size: 13))
                                        .foregroundColor(.white.opacity(0.7))
                                }
                                .padding(.top, 2)
                            }
                        }
                        .padding(.horizontal, 28)

                        // ── Einladungs-Banner ────────────────────────────────
                        if !settings.pendingInviteToken.isEmpty {
                            HStack(alignment: .top, spacing: 12) {
                                Image(systemName: "envelope.badge.fill")
                                    .foregroundColor(MSP.gold)
                                    .font(.system(size: 20))
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(String(localized: "Setup-Einladung erkannt"))
                                        .font(.system(size: 14, weight: .semibold))
                                        .foregroundColor(.white)
                                    Text(String(localized: "Wird automatisch eingelöst."))
                                        .font(.system(size: 12))
                                        .foregroundColor(.white.opacity(0.6))
                                    if !redeemError.isEmpty {
                                        Text(redeemError)
                                            .font(.system(size: 12))
                                            .foregroundColor(.orange)
                                            .textSelection(.enabled)
                                    }
                                }
                                Spacer()
                            }
                            .padding(16)
                            .background(MSP.gold.opacity(0.12))
                            .cornerRadius(14)
                            .overlay(
                                RoundedRectangle(cornerRadius: 14)
                                    .stroke(MSP.gold.opacity(0.25), lineWidth: 1)
                            )
                            .padding(.horizontal, 28)
                            .padding(.top, 20)
                        }

                        // ── Weiter button ────────────────────────────────────
                        Button {
                            settings.serverURL = draftURL.trimmingCharacters(in: .whitespacesAndNewlines)
                            Task { await proceed() }
                        } label: {
                            HStack(spacing: 8) {
                                if redeemBusy {
                                    ProgressView().tint(MSP.navy).scaleEffect(0.85)
                                } else {
                                    Text(String(localized: "Weiter zum Login"))
                                    Image(systemName: "arrow.right.circle.fill")
                                }
                            }
                        }
                        .buttonStyle(GoldButtonStyle())
                        .disabled(redeemBusy || !isValidServerURL(draftURL))
                        .padding(.horizontal, 28)
                        .padding(.top, 28)

                        // ── Hinweis ──────────────────────────────────────────
                        Text(String(localized: "Den QR-Code für die schnelle Einrichtung findest du im Self-Service-Bereich des Management-Portals."))
                            .font(.system(size: 12))
                            .foregroundColor(.white.opacity(0.35))
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, 36)
                            .padding(.top, 24)

                        Spacer(minLength: 48)
                    }
                }
            }
            .toolbar(.hidden, for: .navigationBar)
            .onAppear { draftURL = settings.serverURL }
            .navigationDestination(isPresented: $showLogin) {
                LoginView()
            }
            .sheet(isPresented: $showScanner) {
                QRScannerView { value in
                    showScanner = false
                    guard let v = value, !v.isEmpty else {
                        scanNote = String(localized: "Scan abgebrochen.")
                        return
                    }
                    if v.lowercased().hasPrefix("http://") || v.lowercased().hasPrefix("https://") {
                        draftURL = v
                        scanNote = String(localized: "Server-URL übernommen ✓")
                    } else {
                        scanNote = String(localized: "QR enthält keine gültige Server-URL.")
                    }
                }
                .ignoresSafeArea()
            }
        }
    }

    private func isValidServerURL(_ raw: String) -> Bool {
        let s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url    = URL(string: s),
              let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              let host   = url.host, !host.isEmpty,
              host.contains(".") || host == "localhost"
        else { return false }
        return true
    }

    @MainActor
    private func proceed() async {
        redeemError = ""
        let invite = settings.pendingInviteToken
        guard !invite.isEmpty else { showLogin = true; return }
        redeemBusy = true
        defer { redeemBusy = false }
        do {
            let resp = try await redeemMobileInvite(token: invite)
            settings.bearerToken = resp.bearerToken
            if let u = resp.user {
                if !u.email.isEmpty    { settings.userEmail    = u.email }
                if !u.fullName.isEmpty { settings.userFullName = u.fullName }
                if !u.roleType.isEmpty { settings.userRoleType = u.roleType }
            }
            settings.pendingInviteToken = ""
        } catch {
            settings.pendingInviteToken = ""
            redeemError = String(localized: "Einladung ungültig oder abgelaufen.")
            showLogin   = true
        }
    }

    private struct RedeemResponse { let bearerToken: String; let user: RedeemUser? }
    private struct RedeemUser { let email: String; let fullName: String; let roleType: String }

    private func redeemMobileInvite(token: String) async throws -> RedeemResponse {
        let base = settings.serverURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let baseURL = URL(string: base) else { throw URLError(.badURL) }
        let url = baseURL.appendingPathComponent("api/v1/mobile-invite/redeem")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        req.timeoutInterval = 20
        req.httpBody = try JSONSerialization.data(withJSONObject: [
            "token": token, "device_name": settings.deviceName
        ])
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw URLError(.badServerResponse) }
        if !(200..<300).contains(http.statusCode) {
            var msg = "HTTP \(http.statusCode)"
            if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                if let d = obj["error_description"] as? String { msg = d }
                else if let m = obj["message"] as? String { msg = m }
                else if let e = obj["error"]   as? String { msg = e }
            }
            throw NSError(domain: "MobileInvite", code: http.statusCode,
                          userInfo: [NSLocalizedDescriptionKey: msg])
        }
        guard let obj    = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let bearer = obj["bearer_token"] as? String, !bearer.isEmpty
        else { throw URLError(.cannotParseResponse) }
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
