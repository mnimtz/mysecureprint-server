import SwiftUI
import AuthenticationServices
import PrintixSendCore

struct LoginView: View {

    @EnvironmentObject private var settings: SettingsStore

    @State private var username: String = ""
    @State private var password: String = ""
    @State private var busy: Bool = false
    @State private var entraBusy: Bool = false
    @State private var error: String = ""
    @State private var webAuthAnchor: WebAuthAnchor = WebAuthAnchor()

    private let oauthRedirectURI    = "mysecureprint://oauth/callback"
    private let oauthCallbackScheme = "mysecureprint"

    var body: some View {
        ZStack {
            MSP.navyGradient.ignoresSafeArea()

            ScrollView(showsIndicators: false) {
                VStack(spacing: 0) {

                    // ── Header ──────────────────────────────────────────────
                    BrandHeader(subtitle: String(localized: "Sicher drucken. Überall."))
                        .padding(.top, 64)
                        .padding(.bottom, 40)

                    // ── Form card ───────────────────────────────────────────
                    VStack(spacing: 14) {
                        BrandTextField(
                            label: String(localized: "E-Mail / Benutzername"),
                            icon: "person.fill",
                            text: $username,
                            keyboardType: .emailAddress
                        )
                        BrandTextField(
                            label: String(localized: "Passwort"),
                            icon: "lock.fill",
                            text: $password,
                            isSecure: true
                        )

                        Button {
                            Task { await doPasswordLogin() }
                        } label: {
                            HStack(spacing: 8) {
                                if busy {
                                    ProgressView().tint(MSP.navy)
                                        .scaleEffect(0.85)
                                } else {
                                    Image(systemName: "arrow.right.circle.fill")
                                    Text(String(localized: "Einloggen"))
                                }
                            }
                        }
                        .buttonStyle(GoldButtonStyle())
                        .disabled(busy || entraBusy || username.isEmpty || password.isEmpty)
                        .padding(.top, 4)
                    }
                    .padding(.horizontal, 28)

                    // ── Divider ──────────────────────────────────────────────
                    HStack {
                        Rectangle()
                            .fill(Color.white.opacity(0.15))
                            .frame(height: 1)
                        Text(String(localized: "oder"))
                            .font(.system(size: 12, weight: .medium))
                            .foregroundColor(.white.opacity(0.45))
                            .padding(.horizontal, 12)
                        Rectangle()
                            .fill(Color.white.opacity(0.15))
                            .frame(height: 1)
                    }
                    .padding(.horizontal, 28)
                    .padding(.vertical, 22)

                    // ── Microsoft button ─────────────────────────────────────
                    Button {
                        Task { await startEntraAuthCode() }
                    } label: {
                        HStack(spacing: 10) {
                            if entraBusy {
                                ProgressView().tint(.white).scaleEffect(0.85)
                            } else {
                                _MicrosoftLogo()
                                Text(String(localized: "Mit Microsoft anmelden"))
                            }
                        }
                    }
                    .buttonStyle(CyanButtonStyle())
                    .disabled(busy || entraBusy)
                    .padding(.horizontal, 28)

                    // ── Error ───────────────────────────────────────────────
                    if !error.isEmpty {
                        HStack(alignment: .top, spacing: 8) {
                            Image(systemName: "exclamationmark.triangle.fill")
                                .foregroundColor(.orange)
                                .font(.system(size: 14))
                                .padding(.top, 1)
                            Text(error)
                                .font(.system(size: 13))
                                .foregroundColor(.white.opacity(0.9))
                                .textSelection(.enabled)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .padding(.horizontal, 16)
                        .padding(.vertical, 12)
                        .background(Color.red.opacity(0.18))
                        .cornerRadius(12)
                        .overlay(
                            RoundedRectangle(cornerRadius: 12)
                                .stroke(Color.red.opacity(0.3), lineWidth: 1)
                        )
                        .padding(.horizontal, 28)
                        .padding(.top, 16)
                        .transition(.opacity.combined(with: .move(edge: .top)))
                    }

                    Spacer(minLength: 48)
                }
            }
        }
        .toolbar(.hidden, for: .navigationBar)
        .animation(.easeInOut(duration: 0.2), value: error.isEmpty)
    }

    // MARK: - Password Login

    @MainActor
    private func doPasswordLogin() async {
        error = ""
        busy  = true
        defer { busy = false }
        guard let client = ApiClientFactory.make(baseURL: settings.serverURL, token: nil) else {
            error = String(localized: "Ungültige Server-URL.")
            return
        }
        do {
            let result = try await client.login(
                username: username, password: password, deviceName: settings.deviceName)
            guard let token = result.token, !token.isEmpty else {
                error = String(localized: "Kein Token erhalten.")
                return
            }
            applyLogin(token: token, user: result.user)
        } catch {
            self.error = error.localizedDescription
        }
    }

    // MARK: - Entra Authorization Code + PKCE

    @MainActor
    private func startEntraAuthCode() async {
        error      = ""
        entraBusy  = true
        defer { entraBusy = false }
        guard let client = ApiClientFactory.make(baseURL: settings.serverURL, token: nil) else {
            error = String(localized: "Ungültige Server-URL.")
            return
        }
        let start: EntraAuthCodeStartResponse
        do {
            start = try await client.entraAuthCodeStart(
                deviceName: settings.deviceName, redirectUri: oauthRedirectURI)
        } catch {
            self.error = error.localizedDescription
            return
        }
        guard let sessionId = start.sessionId,
              let urlString = start.authUrl,
              let authUrl   = URL(string: urlString)
        else {
            self.error = String(localized: "Server lieferte keine gültige Microsoft-Login-URL.")
            return
        }
        let callback: URL?
        do {
            callback = try await presentWebAuth(url: authUrl, callbackScheme: oauthCallbackScheme)
        } catch let asError as ASWebAuthenticationSessionError
                    where asError.code == .canceledLogin {
            return
        } catch {
            self.error = error.localizedDescription
            return
        }
        guard let cb   = callback,
              let comps = URLComponents(url: cb, resolvingAgainstBaseURL: false)
        else {
            self.error = String(localized: "Microsoft-Login lieferte keinen Code zurück.")
            return
        }
        let qi = comps.queryItems ?? []
        if let mserr = qi.first(where: { $0.name == "error" })?.value {
            let desc = qi.first(where: { $0.name == "error_description" })?.value
                ?? qi.first(where: { $0.name == "error_subcode" })?.value
            self.error = desc?.isEmpty == false ? desc! : mserr
            return
        }
        guard let code  = qi.first(where: { $0.name == "code" })?.value,
              let state = qi.first(where: { $0.name == "state" })?.value
        else {
            self.error = String(localized: "Microsoft-Login lieferte keinen Code zurück.")
            return
        }
        do {
            let resp   = try await client.entraAuthCodeExchange(
                sessionId: sessionId, code: code, state: state)
            let status = resp.status ?? ""
            if status == "ok", let token = resp.token, !token.isEmpty {
                applyLogin(token: token, user: resp.user)
                return
            }
            self.error = resp.error ?? resp.message
                ?? String(localized: "Login fehlgeschlagen (\(status)).")
        } catch {
            self.error = error.localizedDescription
        }
    }

    @MainActor
    private func presentWebAuth(url: URL, callbackScheme: String) async throws -> URL? {
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<URL?, Error>) in
            let session = ASWebAuthenticationSession(
                url: url, callbackURLScheme: callbackScheme
            ) { callbackURL, error in
                if let error = error { cont.resume(throwing: error) }
                else                 { cont.resume(returning: callbackURL) }
            }
            session.presentationContextProvider = webAuthAnchor
            session.prefersEphemeralWebBrowserSession = false
            session.start()
        }
    }

    @MainActor
    private func applyLogin(token: String, user: UserInfo?) {
        settings.bearerToken  = token
        settings.userEmail    = user?.email    ?? username
        settings.userFullName = user?.fullName ?? ""
        settings.userRoleType = user?.roleType ?? ""
    }
}

// MARK: - Microsoft Logo

private struct _MicrosoftLogo: View {
    var body: some View {
        Grid(horizontalSpacing: 2, verticalSpacing: 2) {
            GridRow {
                Rectangle().fill(Color(brandHex: "#f25022")).frame(width: 9, height: 9)
                Rectangle().fill(Color(brandHex: "#7fba00")).frame(width: 9, height: 9)
            }
            GridRow {
                Rectangle().fill(Color(brandHex: "#00a4ef")).frame(width: 9, height: 9)
                Rectangle().fill(Color(brandHex: "#ffb900")).frame(width: 9, height: 9)
            }
        }
    }
}

// MARK: - Web Auth Anchor

final class WebAuthAnchor: NSObject, ASWebAuthenticationPresentationContextProviding {
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        let scenes = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .filter { $0.activationState == .foregroundActive }
        return scenes.first?.keyWindow
            ?? scenes.first?.windows.first
            ?? ASPresentationAnchor()
    }
}
