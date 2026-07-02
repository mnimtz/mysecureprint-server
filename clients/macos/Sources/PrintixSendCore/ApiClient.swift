import Foundation

// Swift-Pendant zu windows-client/PrintixSend/Services/ApiClient.cs.
// Identische Endpunkte, identisches Auth-Schema (Bearer), identischer
// Server-Vertrag (v6.7.43+ mit 202-Accepted für /desktop/send).
//
// Timeouts: 15 Minuten pro Request — damit LibreOffice-Coldstart oder
// grosse Uploads den Client nicht killen. Eigentlich würde der Server
// ja nach Sekunden mit 202 antworten, aber mit zu aggressivem Timeout
// macht man sich das Leben unnötig schwer.

public enum ApiError: Error, LocalizedError {
    case invalidUrl
    case http(Int, String)
    case decode(String)
    case transport(String)

    public var errorDescription: String? {
        switch self {
        case .invalidUrl:              return "Ungültige Server-URL"
        case .http(let c, let body):
            // Server liefert {"error":"...","code":"..."} — wir ziehen die
            // lesbare Message raus statt den Raw-JSON anzuzeigen. Das Code-
            // Feld nehmen wir als Fallback fuer bekannte Zustaende.
            if let data = body.data(using: .utf8),
               let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                let code = (obj["code"] as? String) ?? ""
                let msg  = (obj["error"] as? String) ?? (obj["message"] as? String) ?? ""
                switch code {
                case "no_printix_user":
                    return "Dein Account ist nicht mit Printix verknüpft. Bitte im Server-Portal den Printix-Login verbinden."
                case "printix_uuid_invalid", "manager_cannot_register_cards":
                    return "Die gespeicherte Printix-User-ID ist ein interner Platzhalter, keine echte UUID. Bitte im Portal die echte UUID aus der Printix-Admin-URL eintragen — oder einen Druckjob über die App schicken, dann wird sie automatisch übernommen."
                case "no_tenant":
                    return "Kein Printix-Tenant fuer diesen Account konfiguriert."
                case "forbidden":
                    return "Keine Berechtigung fuer diese Aktion."
                case "auth_required":
                    return "Anmeldung abgelaufen — bitte erneut einloggen."
                case "printix_error":
                    return "Printix-API hat abgelehnt: \(msg.isEmpty ? "unbekannter Fehler" : msg)"
                case "transform_error":
                    return "Der eingegebene Wert kann nicht ins Zielformat umgewandelt werden."
                default:
                    if !msg.isEmpty { return "Server (HTTP \(c)): \(msg)" }
                    return "Server-Fehler HTTP \(c)"
                }
            }
            return "HTTP \(c) — \(body.prefix(200))"
        case .decode(let msg):         return "Antwort nicht lesbar: \(msg)"
        case .transport(let msg):      return "Verbindung: \(msg)"
        }
    }
}

public final class ApiClient: @unchecked Sendable {
    public let baseUrl: URL
    private let session: URLSession
    private var token: String?
    private let log = AppLogger.shared

    public init(baseUrl: String, token: String? = nil) throws {
        guard let url = URL(string: baseUrl.trimmingCharacters(in: .whitespaces)
                            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))) else {
            throw ApiError.invalidUrl
        }
        self.baseUrl = url
        self.token = token

        let cfg = URLSessionConfiguration.default
        // v0.6.5 (iOS): Timeouts deutlich reduziert. Vorher: 15/30 Min —
        // hat bei einer schlechten Verbindung minutenlanges 'sending'-
        // Spinner ohne klares Feedback verursacht. Jetzt: 60 s pro Request
        // (= zwischen Datenpaketen), 180 s gesamter Upload. Beim Fail
        // sieht der User innerhalb 1-3 Min einen klaren Fehler statt
        // ewiges Hängen.
        cfg.timeoutIntervalForRequest  = 60    // 60 s pro Wartezeit
        cfg.timeoutIntervalForResource = 180   // 3 min gesamter Upload
        cfg.httpAdditionalHeaders = ["User-Agent": "PrintixSend-macOS/\(Self.clientVersion)"]
        self.session = URLSession(configuration: cfg)
    }

    public static let clientVersion = "0.1.0"

    public func setToken(_ t: String?) { self.token = t }

    private func buildRequest(_ path: String, method: String = "GET") -> URLRequest {
        var req = URLRequest(url: baseUrl.appendingPathComponent(path))
        req.httpMethod = method
        if let token = token, !token.isEmpty {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        return req
    }

    private func maskToken(_ t: String?) -> String {
        guard let t = t, !t.isEmpty else { return "<null>" }
        if t.count > 8 {
            let start = t.prefix(4); let end = t.suffix(4)
            return "\(start)…\(end)"
        }
        return "<short>"
    }

    // MARK: - Login

    public func login(username: String, password: String, deviceName: String) async throws -> LoginResponse {
        log.info("POST /desktop/auth/login — user=\(username) device=\(deviceName)")
        var req = buildRequest("desktop/auth/login", method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: String] = [
            "username":    username,
            "password":    password,
            "device_name": deviceName,
        ]
        req.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, resp) = try await session.data(for: req)
        try ensureOk(resp, data)
        let result = try JSONDecoder().decode(LoginResponse.self, from: data)
        if let t = result.token {
            setToken(t)
            log.info("Login ok — token=\(maskToken(t)) user=\(result.user?.username ?? "?")")
        }
        return result
    }

    public func logout() async {
        do {
            var req = buildRequest("desktop/auth/logout", method: "POST")
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            _ = try await session.data(for: req)
            log.info("POST /desktop/auth/logout")
        } catch {
            log.warn("Logout-Fehler (ignoriert): \(error)")
        }
    }

    // MARK: - Me

    public func me() async throws -> UserInfo? {
        log.info("GET /desktop/me")
        let req = buildRequest("desktop/me")
        let (data, resp) = try await session.data(for: req)
        try ensureOk(resp, data)
        if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let userDict = obj["user"] as? [String: Any] {
            let userData = try JSONSerialization.data(withJSONObject: userDict)
            return try? JSONDecoder().decode(UserInfo.self, from: userData)
        }
        return nil
    }

    /// v1.0.2: /desktop/me mit dem tenant-weiten Delegation-Flag. Wird von
    /// der iOS-App benutzt um den User-Toggle „Delegation-Druck erlauben"
    /// in Settings auszublenden wenn der Server-Admin das Feature global
    /// deaktiviert hat.
    public struct MeEnvelope {
        public let user: UserInfo?
        public let delegationAllowed: Bool
        public let employeesCanManageCards: Bool
    }

    public func meWithFlags() async throws -> MeEnvelope {
        log.info("GET /desktop/me (with flags)")
        let req = buildRequest("desktop/me")
        let (data, resp) = try await session.data(for: req)
        try ensureOk(resp, data)
        var user: UserInfo? = nil
        var delAllowed = true
        var empCards = false
        if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            if let userDict = obj["user"] as? [String: Any],
               let userData = try? JSONSerialization.data(withJSONObject: userDict) {
                user = try? JSONDecoder().decode(UserInfo.self, from: userData)
            }
            if let flag = obj["delegation_allowed"] as? Bool {
                delAllowed = flag
            }
            if let flag = obj["employees_can_manage_cards"] as? Bool {
                empCards = flag
            }
        }
        return MeEnvelope(user: user, delegationAllowed: delAllowed, employeesCanManageCards: empCards)
    }

    // MARK: - Targets

    public func targets() async throws -> [Target] {
        log.info("GET /desktop/targets")
        let req = buildRequest("desktop/targets")
        let (data, resp) = try await session.data(for: req)
        try ensureOk(resp, data)
        let result = try JSONDecoder().decode(TargetsResponse.self, from: data)
        log.info("Targets: \(result.targets.count) Eintrag/Einträge")
        return result.targets
    }

    /// v0.6.7: Volle TargetsResponse inkl. `user_can_choose`-Flag —
    /// fuer iOS-Clients die einen Queue-Picker zeigen wollen wenn der
    /// Admin "User darf Queue waehlen" aktiviert hat.
    public func targetsFull() async throws -> TargetsResponse {
        log.info("GET /desktop/targets")
        let req = buildRequest("desktop/targets")
        let (data, resp) = try await session.data(for: req)
        try ensureOk(resp, data)
        let result = try JSONDecoder().decode(TargetsResponse.self, from: data)
        log.info("Targets: \(result.targets.count) Eintrag/Einträge can_choose=\(result.userCanChoose ?? false)")
        return result
    }

    /// v0.6.7: Liste aller Tenant-Queues fuer den Queue-Picker
    /// (sortiert vom Server: Anywhere zuerst).
    public func listQueues() async throws -> QueuesResponse {
        log.info("GET /desktop/queues")
        let req = buildRequest("desktop/queues")
        let (data, resp) = try await session.data(for: req)
        try ensureOk(resp, data)
        return try JSONDecoder().decode(QueuesResponse.self, from: data)
    }

    // MARK: - Send

    public func send(filePath: String, targetId: String,
                     comment: String? = nil,
                     copies: Int = 1,
                     color: Bool = false,
                     duplex: Bool = false) async throws -> SendResult {
        let url = URL(fileURLWithPath: filePath)
        let fileData = try Data(contentsOf: url)
        let filename = url.lastPathComponent
        return try await sendData(fileData, filename: filename, targetId: targetId,
                                  comment: comment, copies: copies, color: color, duplex: duplex)
    }

    /// In-Memory-Variante: nötig für den iOS-Share-Extension-Pfad, der oft
    /// nur Data erhält (z. B. bereits aus Bild→PDF-Rendering) und nichts
    /// auf Disk schreiben möchte.
    public func sendData(_ fileData: Data, filename: String, targetId: String,
                         comment: String? = nil,
                         copies: Int = 1,
                         color: Bool = false,
                         duplex: Bool = false) async throws -> SendResult {
        log.info("POST /desktop/send — target=\(targetId) file=\(filename) size=\(fileData.count)")

        let boundary = "Boundary-\(UUID().uuidString)"
        var req = buildRequest("desktop/send", method: "POST")
        req.setValue("multipart/form-data; boundary=\(boundary)",
                     forHTTPHeaderField: "Content-Type")

        var body = Data()
        func append(_ s: String) { body.append(Data(s.utf8)) }

        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"target_id\"\r\n\r\n")
        append("\(targetId)\r\n")

        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"copies\"\r\n\r\n")
        append("\(copies)\r\n")

        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"color\"\r\n\r\n")
        append(color ? "1\r\n" : "\r\n")

        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"duplex\"\r\n\r\n")
        append(duplex ? "1\r\n" : "\r\n")

        if let c = comment, !c.isEmpty {
            append("--\(boundary)\r\n")
            append("Content-Disposition: form-data; name=\"comment\"\r\n\r\n")
            append("\(c)\r\n")
        }

        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n")
        let ext = (filename as NSString).pathExtension
        append("Content-Type: \(guessMime(ext))\r\n\r\n")
        body.append(fileData)
        append("\r\n--\(boundary)--\r\n")

        req.httpBody = body

        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse else {
            throw ApiError.transport("Keine HTTP-Antwort")
        }

        // v6.7.43: Server schickt 202 Accepted mit ok:true. Alle 2xx = ok.
        if (200..<300).contains(http.statusCode) {
            let result = (try? JSONDecoder().decode(SendResult.self, from: data))
                ?? SendResult(ok: true, status: "queued", jobId: nil, printixJobId: nil,
                              target: targetId, filename: filename, size: fileData.count,
                              ownerEmail: nil, error: nil, code: nil, message: nil)
            log.info("Send ok — status=\(result.status ?? "-") job_id=\(result.jobId ?? "")")
            return result
        }

        let bodyStr = String(data: data, encoding: .utf8) ?? ""
        log.warn("Send-Fehler: HTTP \(http.statusCode) — \(bodyStr.prefix(400))")
        if let parsed = try? JSONDecoder().decode(SendResult.self, from: data) {
            return parsed
        }
        return SendResult(ok: false, status: nil, jobId: nil, printixJobId: nil,
                          target: targetId, filename: filename, size: fileData.count,
                          ownerEmail: nil, error: "HTTP \(http.statusCode)",
                          code: "http_error", message: bodyStr)
    }

    // MARK: - Jobs

    /// Letzte Druck-Jobs des angemeldeten Users. Server-Endpoint
    /// `/desktop/me/jobs` seit v0.6.0. `limit` ist optional und wird
    /// als Query-Parameter angehaengt.
    public func myJobs(limit: Int = 30) async throws -> Data {
        log.info("GET /desktop/me/jobs — limit=\(limit)")
        var comps = URLComponents(
            url: baseUrl.appendingPathComponent("desktop/me/jobs"),
            resolvingAgainstBaseURL: false
        )
        comps?.queryItems = [URLQueryItem(name: "limit", value: String(limit))]
        guard let url = comps?.url else { throw ApiError.invalidUrl }
        var req = URLRequest(url: url)
        if let token = token, !token.isEmpty {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        req.timeoutInterval = 15
        let (data, resp) = try await session.data(for: req)
        try ensureOk(resp, data)
        return data
    }

    // MARK: - Entra Device-Code

    public func entraStart(deviceName: String) async throws -> EntraStartResponse {
        log.info("POST /desktop/auth/entra/start")
        var req = buildRequest("desktop/auth/entra/start", method: "POST")
        // Server-Endpoint nutzt FastAPI Form() — daher x-www-form-urlencoded,
        // nicht JSON.
        req.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        req.httpBody = Self.formEncode(["device_name": deviceName])
        let (data, resp) = try await session.data(for: req)
        try ensureOk(resp, data)
        return try JSONDecoder().decode(EntraStartResponse.self, from: data)
    }

    public func entraPoll(sessionId: String) async throws -> EntraPollResponse {
        var req = buildRequest("desktop/auth/entra/poll", method: "POST")
        // Server-Endpoint nutzt FastAPI Form(session_id) — daher form-encoded.
        req.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        req.httpBody = Self.formEncode(["session_id": sessionId])
        let (data, resp) = try await session.data(for: req)
        try ensureOk(resp, data)
        return try JSONDecoder().decode(EntraPollResponse.self, from: data)
    }

    // MARK: - Entra Authorization Code + PKCE (iOS, v6.7.120+)

    /// Startet den Auth-Code-Flow. Der Server erzeugt PKCE-Paar + State,
    /// gibt die Microsoft-Login-URL zurueck. Der Client oeffnet diese
    /// URL in einer `ASWebAuthenticationSession` mit `callbackURLScheme`
    /// passend zum `redirectUri` (z.B. `printix-mobile`).
    public func entraAuthCodeStart(deviceName: String,
                                    redirectUri: String) async throws
                                    -> EntraAuthCodeStartResponse {
        log.info("POST /desktop/auth/entra/authcode/start")
        var req = buildRequest("desktop/auth/entra/authcode/start", method: "POST")
        req.setValue("application/x-www-form-urlencoded",
                     forHTTPHeaderField: "Content-Type")
        req.httpBody = Self.formEncode([
            "device_name":  deviceName,
            "redirect_uri": redirectUri,
        ])
        let (data, resp) = try await session.data(for: req)
        try ensureOk(resp, data)
        do {
            return try JSONDecoder().decode(EntraAuthCodeStartResponse.self, from: data)
        } catch {
            // Server hat zwar 2xx geliefert, aber das JSON passt nicht zum
            // erwarteten Schema. Typischer Fall: Server-Version zu alt
            // (Endpoint existiert nicht, FastAPI/Reverse-Proxy liefert
            // andere 2xx-HTML/JSON). Body in den Fehler legen, damit der
            // User im Login-Screen sieht WAS falsch ist.
            let preview = String(data: data, encoding: .utf8)?.prefix(300) ?? "<binary>"
            throw ApiError.decode(
                "AuthCode-Start: Antwort passt nicht zum erwarteten Format. " +
                "Server-Version moeglicherweise zu alt (>=6.7.120 noetig). " +
                "Body: \(preview)"
            )
        }
    }

    /// Tauscht den von Microsoft per Custom-URL-Scheme zurueckgereichten
    /// `code` (zusammen mit dem `state` zum CSRF-Schutz) gegen einen
    /// Desktop-Token. Wiederverwendet `EntraPollResponse` fuer die
    /// Antwort-Struktur, da Felder identisch sind (`status`, `token`,
    /// `user`, `error`).
    public func entraAuthCodeExchange(sessionId: String,
                                       code: String,
                                       state: String) async throws
                                       -> EntraPollResponse {
        log.info("POST /desktop/auth/entra/authcode/exchange")
        var req = buildRequest("desktop/auth/entra/authcode/exchange", method: "POST")
        req.setValue("application/x-www-form-urlencoded",
                     forHTTPHeaderField: "Content-Type")
        req.httpBody = Self.formEncode([
            "session_id": sessionId,
            "code":       code,
            "state":      state,
        ])
        let (data, resp) = try await session.data(for: req)
        try ensureOk(resp, data)
        return try JSONDecoder().decode(EntraPollResponse.self, from: data)
    }

    /// Hilfs-Encoder für `application/x-www-form-urlencoded`-Bodies.
    private static func formEncode(_ fields: [String: String]) -> Data {
        var allowed = CharacterSet.urlQueryAllowed
        allowed.remove(charactersIn: "+&=")
        let pairs = fields.map { (k, v) -> String in
            let ek = k.addingPercentEncoding(withAllowedCharacters: allowed) ?? k
            let ev = v.addingPercentEncoding(withAllowedCharacters: allowed) ?? v
            return "\(ek)=\(ev)"
        }
        return pairs.joined(separator: "&").data(using: .utf8) ?? Data()
    }

    // MARK: - Management (iOS "Printix Management" Tab, Server v6.7.66+)

    /// Live-Zählerübersicht fürs Dashboard des Management-Tabs.
    /// Jeder Aufruf feuert Printix-Requests — Server cached nicht.
    public func managementStats() async throws -> MgmtStatsResponse {
        log.info("GET /desktop/management/stats")
        let req = buildRequest("desktop/management/stats")
        let (data, resp) = try await session.data(for: req)
        try ensureOk(resp, data)
        return try JSONDecoder().decode(MgmtStatsResponse.self, from: data)
    }

    public func managementPrinters() async throws -> MgmtPrintersResponse {
        log.info("GET /desktop/management/printers")
        let req = buildRequest("desktop/management/printers")
        let (data, resp) = try await session.data(for: req)
        try ensureOk(resp, data)
        return try JSONDecoder().decode(MgmtPrintersResponse.self, from: data)
    }

    public func managementUsers() async throws -> MgmtUsersResponse {
        log.info("GET /desktop/management/users")
        let req = buildRequest("desktop/management/users")
        let (data, resp) = try await session.data(for: req)
        try ensureOk(resp, data)
        return try JSONDecoder().decode(MgmtUsersResponse.self, from: data)
    }

    public func managementWorkstations() async throws -> MgmtWorkstationsResponse {
        log.info("GET /desktop/management/workstations")
        let req = buildRequest("desktop/management/workstations")
        let (data, resp) = try await session.data(for: req)
        try ensureOk(resp, data)
        return try JSONDecoder().decode(MgmtWorkstationsResponse.self, from: data)
    }

    // MARK: - Version

    public func latestVersion() async -> VersionResponse? {
        do {
            let req = buildRequest("desktop/client/latest-version")
            let (data, resp) = try await session.data(for: req)
            guard (resp as? HTTPURLResponse)?.statusCode == 200 else { return nil }
            return try? JSONDecoder().decode(VersionResponse.self, from: data)
        } catch {
            return nil
        }
    }

    // MARK: - Cards (iOS-Tab, Server v6.7.90+)

    /// Liste der eigenen Karten des angemeldeten Users. Gate auf Server-
    /// Seite: role_type ∈ {admin, user} — Employees bekommen 403.
    public func listCards() async throws -> [Card] {
        log.info("GET /desktop/cards")
        let req = buildRequest("desktop/cards")
        let (data, resp) = try await session.data(for: req)
        try ensureOk(resp, data)
        let result = try JSONDecoder().decode(CardsResponse.self, from: data)
        log.info("Cards: \(result.cards.count) Eintrag/Einträge")
        return result.cards
    }

    /// Alle Transformations-Profile (builtin + custom) des Tenants.
    public func listCardProfiles() async throws -> [CardProfile] {
        let (profiles, _) = try await listCardProfilesWithDefault()
        return profiles
    }

    /// Wie `listCardProfiles()`, liefert zusaetzlich die vom Admin
    /// gesetzte Default-Profil-ID zurueck (leer = kein Default → Client
    /// soll Picker zeigen; gesetzt → Client nutzt dieses Profil still).
    public func listCardProfilesWithDefault() async throws -> ([CardProfile], String) {
        log.info("GET /desktop/cards/profiles")
        let req = buildRequest("desktop/cards/profiles")
        let (data, resp) = try await session.data(for: req)
        try ensureOk(resp, data)
        let result = try JSONDecoder().decode(CardProfilesResponse.self, from: data)
        return (result.profiles, result.defaultProfileId ?? "")
    }

    /// Dry-Run: zeigt wie `rawValue` mit dem gewaehlten Profil transformiert
    /// wuerde, ohne zu speichern und ohne Printix-Call. Fuer die Preview-
    /// Anzeige im Add-Dialog.
    public func previewCard(rawValue: String, profileId: String? = nil) async throws -> CardPreview {
        log.info("POST /desktop/cards/preview — profile=\(profileId ?? "-")")
        var req = buildRequest("desktop/cards/preview", method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        // Preview ist reiner Transform, kein Printix-Call — 15s reichen.
        req.timeoutInterval = 15
        var body: [String: String] = ["raw_value": rawValue]
        if let p = profileId, !p.isEmpty { body["profile_id"] = p }
        req.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, resp) = try await session.data(for: req)
        try ensureOk(resp, data)
        let result = try JSONDecoder().decode(CardPreviewResponse.self, from: data)
        return result.preview
    }

    /// Neue Karte anlegen — Server transformiert, pusht an Printix, speichert
    /// lokales Mapping und liefert den fertigen Card-Record zurueck.
    public func createCard(rawValue: String,
                           profileId: String? = nil,
                           notes: String? = nil) async throws -> Card {
        log.info("POST /desktop/cards — profile=\(profileId ?? "-")")
        var req = buildRequest("desktop/cards", method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        // Card-Create macht 3 Printix-API-Calls (list before/after + register).
        // 60s ist grosszuegig und sorgt dafuer, dass der User bei Hang
        // innerhalb einer Minute einen Fehler sieht — nicht nach 15min.
        req.timeoutInterval = 60
        var body: [String: String] = ["raw_value": rawValue]
        if let p = profileId, !p.isEmpty { body["profile_id"] = p }
        if let n = notes, !n.isEmpty { body["notes"] = n }
        req.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, resp) = try await session.data(for: req)
        try ensureOk(resp, data)
        let result = try JSONDecoder().decode(CardCreateResponse.self, from: data)
        log.info("Card angelegt — id=\(result.card.id) printix=\(result.card.printixCardId)")
        return result.card
    }

    /// Karte loeschen. Server ruft zuerst Printix DELETE auf — schlaegt das
    /// fehl, bleibt das lokale Mapping erhalten damit der User es nochmal
    /// probieren kann.
    public func deleteCard(id: Int) async throws {
        log.info("DELETE /desktop/cards/\(id)")
        let req = buildRequest("desktop/cards/\(id)", method: "DELETE")
        let (data, resp) = try await session.data(for: req)
        try ensureOk(resp, data)
    }

    // MARK: - Helpers

    private func ensureOk(_ resp: URLResponse, _ data: Data) throws {
        guard let http = resp as? HTTPURLResponse else {
            throw ApiError.transport("Keine HTTP-Antwort")
        }
        if !(200..<300).contains(http.statusCode) {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw ApiError.http(http.statusCode, body)
        }
    }

    private func guessMime(_ ext: String) -> String {
        switch ext.lowercased() {
        case "pdf":  return "application/pdf"
        case "png":  return "image/png"
        case "jpg", "jpeg": return "image/jpeg"
        case "gif":  return "image/gif"
        case "txt":  return "text/plain"
        case "docx": return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        case "xlsx": return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        case "pptx": return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        case "doc":  return "application/msword"
        case "xls":  return "application/vnd.ms-excel"
        case "ppt":  return "application/vnd.ms-powerpoint"
        default:     return "application/octet-stream"
        }
    }
}
