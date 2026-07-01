import SwiftUI
import AuthenticationServices
import PrintixSendCore

/// Login-Bildschirm mit zwei Pfaden:
///   1) klassisches Username/Passwort gegen /desktop/auth/login
///   2) Entra Authorization Code + PKCE (Microsoft Login) — oeffnet
///      eine ASWebAuthenticationSession (in-app Safari-Sheet), MS
///      redirected per Custom-URL-Scheme zurueck, App tauscht den
///      `code` ueber den Server gegen einen Bearer-Token.
///
/// Erfolgreicher Login speichert Bearer-Token + User-Info im
/// SettingsStore, die App schaltet danach automatisch auf den
/// Haupt-Tab-Flow um (siehe ContentView).
struct LoginView: View {

    @EnvironmentObject private var settings: SettingsStore

    @State private var username: String = ""
    @State private var password: String = ""
    @State private var busy: Bool = false
    @State private var entraBusy: Bool = false
    @State private var error: String = ""

    /// Halte den Presentation-Provider als State, damit er nicht
    /// vor Sheet-Ende deallokiert wird (ASWebAuthenticationSession
    /// braucht ihn die ganze Zeit).
    @State private var webAuthAnchor: WebAuthAnchor = WebAuthAnchor()

    /// Custom-URL-Scheme-Redirect; muss im Info.plist als
    /// CFBundleURLScheme registriert sein UND in der Entra-App-
    /// Registration als Mobile-Redirect-URI hinterlegt werden.
    private let oauthRedirectURI = "mysecureprint://oauth/callback"
    private let oauthCallbackScheme = "mysecureprint"

    var body: some View {
        Form {
            Section(String(localized: "Anmeldung")) {
                TextField(String(localized: "E-Mail / Benutzername"), text: $username)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.emailAddress)
                SecureField(String(localized: "Passwort"), text: $password)
            }

            Section {
                Button {
                    Task { await doPasswordLogin() }
                } label: {
                    HStack {
                        Spacer()
                        if busy { ProgressView() }
                        else {
                            Image(systemName: "lock.open.fill")
                            Text(String(localized: "Einloggen")).fontWeight(.semibold)
                        }
                        Spacer()
                    }
                }
                .disabled(busy || entraBusy || username.isEmpty || password.isEmpty)
            }

            Section(String(localized: "Oder mit Microsoft")) {
                Button {
                    Task { await startEntraAuthCode() }
                } label: {
                    HStack {
                        if entraBusy { ProgressView() }
                        else { Image(systemName: "person.badge.key.fill") }
                        Text(String(localized: "Per Microsoft-Konto einloggen"))
                    }
                }
                .disabled(busy || entraBusy)
            }

            if !error.isEmpty {
                Section(String(localized: "Fehler")) {
                    Text(error).foregroundColor(.red).textSelection(.enabled)
                }
            }
        }
        .navigationTitle("Login")
    }

    @MainActor
    private func doPasswordLogin() async {
        error = ""
        busy = true
        defer { busy = false }

        guard let client = ApiClientFactory.make(baseURL: settings.serverURL, token: nil) else {
            error = String(localized: "Ungültige Server-URL.")
            return
        }
        do {
            let result = try await client.login(username: username,
                                                password: password,
                                                deviceName: settings.deviceName)
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
        error = ""
        entraBusy = true
        defer { entraBusy = false }

        guard let client = ApiClientFactory.make(baseURL: settings.serverURL, token: nil) else {
            error = String(localized: "Ungültige Server-URL.")
            return
        }

        // 1) Server-seitig PKCE-Paar erzeugen lassen, MS-Auth-URL holen
        let start: EntraAuthCodeStartResponse
        do {
            start = try await client.entraAuthCodeStart(
                deviceName: settings.deviceName,
                redirectUri: oauthRedirectURI)
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

        // 2) ASWebAuthenticationSession öffnen — MS-Login findet im
        //    in-app Safari-Sheet statt. iOS schliesst das Sheet
        //    automatisch sobald MS auf den oauthCallbackScheme
        //    redirected.
        let callback: URL?
        do {
            callback = try await presentWebAuth(url: authUrl,
                                                callbackScheme: oauthCallbackScheme)
        } catch let asError as ASWebAuthenticationSessionError
                    where asError.code == .canceledLogin {
            // User hat das Sheet abgebrochen — keine Fehlermeldung
            return
        } catch {
            self.error = error.localizedDescription
            return
        }

        guard let cb = callback,
              let comps = URLComponents(url: cb, resolvingAgainstBaseURL: false)
        else {
            self.error = String(localized: "Microsoft-Login lieferte keinen Code zurück.")
            return
        }
        let qi = comps.queryItems ?? []
        // Microsoft kann auch `error=access_denied&error_description=...`
        // zurueckliefern (Consent verweigert, Conditional Access blockt,
        // Tenant-Mismatch etc.). Wir zeigen die echte Begruendung statt
        // eines generischen "kein Code". Reihenfolge: error_description
        // (menschenlesbar), error_code, error.
        if let mserr = qi.first(where: { $0.name == "error" })?.value {
            let desc = qi.first(where: { $0.name == "error_description" })?.value
                ?? qi.first(where: { $0.name == "error_subcode" })?.value
            self.error = desc?.isEmpty == false ? desc! : mserr
            return
        }
        guard let code = qi.first(where: { $0.name == "code" })?.value,
              let state = qi.first(where: { $0.name == "state" })?.value
        else {
            self.error = String(localized: "Microsoft-Login lieferte keinen Code zurück.")
            return
        }

        // 3) Code + state an Server schicken, der tauscht ihn gegen
        //    einen MCP-Bearer-Token. PKCE-Verifier bleibt serverseitig.
        do {
            let resp = try await client.entraAuthCodeExchange(
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

    /// Brückt `ASWebAuthenticationSession` in async/await. Ruft den
    /// Completion-Handler in eine Continuation, gibt die Callback-URL
    /// zurueck oder wirft den ASError (z.B. `.canceledLogin`).
    @MainActor
    private func presentWebAuth(url: URL,
                                callbackScheme: String) async throws -> URL? {
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<URL?, Error>) in
            let session = ASWebAuthenticationSession(
                url: url, callbackURLScheme: callbackScheme
            ) { callbackURL, error in
                if let error = error {
                    cont.resume(throwing: error)
                } else {
                    cont.resume(returning: callbackURL)
                }
            }
            session.presentationContextProvider = webAuthAnchor
            // SSO-Cookies behalten — User muss sich nicht jedes Mal neu
            // anmelden, MFA bleibt gemerkt.
            session.prefersEphemeralWebBrowserSession = false
            session.start()
        }
    }

    @MainActor
    private func applyLogin(token: String, user: UserInfo?) {
        settings.bearerToken  = token
        settings.userEmail    = user?.email ?? username
        settings.userFullName = user?.fullName ?? ""
        settings.userRoleType = user?.roleType ?? ""
        // ContentView beobachtet isLoggedIn und wechselt automatisch auf Main-Flow.
    }
}

/// Kleiner Presentation-Anchor fuer ASWebAuthenticationSession.
/// Liefert das Key-Window der App — ohne diesen Provider wirft iOS
/// einen Fehler beim Start der Session.
final class WebAuthAnchor: NSObject, ASWebAuthenticationPresentationContextProviding {
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        // iOS 15+: erstes Foreground-Window-Scene
        let scenes = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .filter { $0.activationState == .foregroundActive }
        return scenes.first?.keyWindow
            ?? scenes.first?.windows.first
            ?? ASPresentationAnchor()
    }
}
