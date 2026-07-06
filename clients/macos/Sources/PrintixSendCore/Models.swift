import Foundation

// DTOs für die Desktop-API des Printix-MCP-Servers.
// 1:1-Pendant zu windows-client/PrintixSend/Models/*.cs — die gleichen
// JSON-Felder, damit beide Clients dieselbe Server-Version nutzen.

public struct LoginResponse: Codable, Sendable {
    public let ok: Bool?
    public let token: String?
    public let user: UserInfo?

    public init(ok: Bool? = nil, token: String? = nil, user: UserInfo? = nil) {
        self.ok = ok
        self.token = token
        self.user = user
    }
}

public struct UserInfo: Codable, Sendable {
    public let userId: Int?
    public let username: String?
    public let email: String?
    public let fullName: String?
    public let roleType: String?
    public let deviceName: String?

    enum CodingKeys: String, CodingKey {
        case userId = "user_id"
        case username, email
        case fullName = "full_name"
        case roleType = "role_type"
        case deviceName = "device_name"
    }

    public init(userId: Int? = nil, username: String? = nil, email: String? = nil,
                fullName: String? = nil, roleType: String? = nil, deviceName: String? = nil) {
        self.userId = userId
        self.username = username
        self.email = email
        self.fullName = fullName
        self.roleType = roleType
        self.deviceName = deviceName
    }
}

public struct Target: Codable, Identifiable, Hashable, Sendable {
    public let id: String
    public let label: String
    public let type: String?
    public let subtitle: String?
    /// v0.6.5 (iOS): vom Server gesetzt fuer das Default-Ziel
    /// (print:self mit resolved 3-tier Queue). Wird vom Prefetch
    /// genutzt, um direkt nach Login die richtige Queue zu picken.
    public let isDefault: Bool?

    enum CodingKeys: String, CodingKey {
        case id, label, type, subtitle
        case isDefault = "is_default"
    }
}

public struct TargetsResponse: Codable, Sendable {
    public let targets: [Target]
    /// v0.6.7: Server-Flag — wenn true, darf der User aus
    /// allen Tenant-Queues via /desktop/queues eine andere
    /// Druckqueue waehlen (Admin-Setting "Allow user queue override").
    public let userCanChoose: Bool?

    enum CodingKeys: String, CodingKey {
        case targets
        case userCanChoose = "user_can_choose"
    }
}

/// v0.6.7: Eine einzelne Queue aus /desktop/queues (Tenant-Queue-Liste).
public struct QueueItem: Codable, Identifiable, Hashable, Sendable {
    public let id: String           // "print:queue:<queue_id>"
    public let queueId: String
    public let queueName: String
    public let printerId: String?
    public let printerName: String?
    public let vendor: String?
    public let model: String?
    public let isAnywhere: Bool?
    public let location: String?

    enum CodingKeys: String, CodingKey {
        case id, vendor, model, location
        case queueId      = "queue_id"
        case queueName    = "queue_name"
        case printerId    = "printer_id"
        case printerName  = "printer_name"
        case isAnywhere   = "is_anywhere"
    }
}

public struct QueuesResponse: Codable, Sendable {
    public let queues: [QueueItem]
    public let available: Bool?
    public let count: Int?
}

public struct SendResult: Codable, Sendable {
    public let ok: Bool?
    public let status: String?
    public let jobId: String?
    public let printixJobId: String?
    public let target: String?
    public let filename: String?
    public let size: Int?
    public let ownerEmail: String?
    public let error: String?
    public let code: String?
    public let message: String?

    enum CodingKeys: String, CodingKey {
        case ok, status, target, filename, size, error, code, message
        case jobId = "job_id"
        case printixJobId = "printix_job_id"
        case ownerEmail = "owner_email"
    }
}

public struct EntraStartResponse: Codable, Sendable {
    /// Server-seitig erzeugte Session-ID, die beim Poll mitgeschickt wird.
    /// Der echte Microsoft device_code bleibt aus Sicherheitsgründen serverseitig
    /// in `desktop_entra_pending` und wird nie an den Client geleakt.
    public let sessionId: String?
    public let userCode: String?
    public let verificationUri: String?
    public let expiresIn: Int?
    public let interval: Int?
    public let message: String?

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case userCode = "user_code"
        case verificationUri = "verification_uri"
        case expiresIn = "expires_in"
        case interval, message
    }
}

/// Antwort auf `/desktop/auth/entra/authcode/start` — der Server hat
/// das PKCE-Paar erzeugt, die Microsoft-Auth-URL zusammengebaut und
/// alles serverseitig in `desktop_entra_authcode_pending` persistiert.
/// Der Client öffnet `authUrl` in einer ASWebAuthenticationSession und
/// schickt anschliessend `sessionId` + `code` + `state` an den
/// Exchange-Endpoint.
public struct EntraAuthCodeStartResponse: Codable, Sendable {
    public let sessionId: String?
    public let authUrl: String?
    public let state: String?
    public let expiresIn: Int?

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case authUrl   = "auth_url"
        case state
        case expiresIn = "expires_in"
    }
}

public struct EntraPollResponse: Codable, Sendable {
    public let ok: Bool?
    public let status: String?     // "pending" | "success" | "error"
    public let token: String?
    public let user: UserInfo?
    public let error: String?
    public let message: String?
}

// MARK: - Management (iOS-Tab "Printix Management", Server v6.7.66+)
//
// Rein Live-Abfragen an /desktop/management/*. Kein Cache, kein Poller.
// Gleiches JSON-Schema wird vom Server in desktop_management_routes.py
// erzeugt.

public struct MgmtStatsBucket: Codable, Sendable {
    public let total: Int?
    public let online: Int?
    public let available: Bool?
    public let error: String?
}

public struct MgmtTenantInfo: Codable, Sendable {
    // Server schickt die interne DB-PK (int) oder in manchen Konfigs auch
    // den Printix-Tenant-UUID-String — wir akzeptieren beides.
    public let id: String?
    public let name: String?

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        if let s = try? c.decode(String.self, forKey: .id) {
            self.id = s
        } else if let i = try? c.decode(Int.self, forKey: .id) {
            self.id = String(i)
        } else {
            self.id = nil
        }
        self.name = try? c.decode(String.self, forKey: .name)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encodeIfPresent(id, forKey: .id)
        try c.encodeIfPresent(name, forKey: .name)
    }

    enum CodingKeys: String, CodingKey { case id, name }
}

public struct MgmtStatsResponse: Codable, Sendable {
    public let printers: MgmtStatsBucket?
    public let users: MgmtStatsBucket?
    public let workstations: MgmtStatsBucket?
    public let tenant: MgmtTenantInfo?
}

public struct MgmtPrinter: Codable, Identifiable, Hashable, Sendable {
    public let id: String
    public let queueId: String?
    public let name: String
    public let model: String?
    public let location: String?
    public let status: String?
    public let isOnline: Bool?

    enum CodingKeys: String, CodingKey {
        case id, name, model, location, status
        case queueId  = "queue_id"
        case isOnline = "is_online"
    }
}

public struct MgmtPrintersResponse: Codable, Sendable {
    public let printers: [MgmtPrinter]
    public let available: Bool?
}

public struct MgmtUser: Codable, Identifiable, Hashable, Sendable {
    public let id: String
    public let email: String?
    public let name: String?
    public let role: String?
}

public struct MgmtUsersResponse: Codable, Sendable {
    public let users: [MgmtUser]
    public let available: Bool?
}

public struct MgmtWorkstation: Codable, Identifiable, Hashable, Sendable {
    public let id: String
    public let hostname: String
    public let userEmail: String?
    public let lastSeen: String?
    public let isOnline: Bool?

    enum CodingKeys: String, CodingKey {
        case id, hostname
        case userEmail = "user_email"
        case lastSeen  = "last_seen"
        case isOnline  = "is_online"
    }
}

public struct MgmtWorkstationsResponse: Codable, Sendable {
    public let workstations: [MgmtWorkstation]
    public let available: Bool?
}

// MARK: - Printer Detail (GET /desktop/management/printers/{id})
//
// Wraps the raw Printix API response. `supplies` is future-proof:
// Printix currently does not expose toner levels via its REST API.
// The field is decoded from the response if it ever appears, otherwise nil.

public struct MgmtPrinterDetail: Codable, Sendable {
    public let id: String
    public let queueId: String
    public let printer: PrinterRawData?

    enum CodingKeys: String, CodingKey {
        case id, printer
        case queueId = "queue_id"
    }
}

public struct PrinterRawData: Codable, Sendable {
    public let serialNo: String?
    public let vendor: String?
    public let connectionStatus: String?
    public let capabilities: PrinterCapabilities?
    public let supplies: [PrinterSupply]?
}

public struct PrinterCapabilities: Codable, Sendable {
    public let color: Bool?
    public let paperSizes: [String]?

    enum CodingKeys: String, CodingKey {
        case color
        case paperSizes = "paperSizes"
    }
}

public struct PrinterSupply: Codable, Sendable {
    public let color: String?    // "black" | "cyan" | "magenta" | "yellow"
    public let level: Int?
    public let maxLevel: Int?

    public var percent: Double? {
        guard let l = level, let m = maxLevel, m > 0 else { return nil }
        return Double(l) / Double(m) * 100
    }
}

// MARK: - Workstation Detail (GET /desktop/management/workstations/{id})

public struct MgmtWorkstationDetail: Codable, Sendable {
    public let id: String
    public let hostname: String
    public let userEmail: String?
    public let isOnline: Bool
    public let lastSeen: String?
    public let lastConnectTime: String?
    public let lastDisconnectTime: String?
    public let siteId: String?
    public let description: String?
    public let networkIds: [String]?

    enum CodingKeys: String, CodingKey {
        case id, hostname, description
        case userEmail = "user_email"
        case isOnline = "is_online"
        case lastSeen = "last_seen"
        case lastConnectTime = "last_connect_time"
        case lastDisconnectTime = "last_disconnect_time"
        case siteId = "site_id"
        case networkIds = "network_ids"
    }
}

// MARK: - User Detail (GET /desktop/management/users/{id})

public struct MgmtUserCard: Codable, Sendable {
    public let id: String
    public let cardType: String?
    public let number: String?

    enum CodingKeys: String, CodingKey {
        case id, number
        case cardType = "card_type"
    }
}

public struct MgmtUserDetail: Codable, Sendable {
    public let id: String
    public let email: String?
    public let name: String?
    public let role: String?
    public let language: String?
    public let roles: [String]?
    public let authMethods: [String]?
    public let created: String?
    public let modified: String?
    public let idCode: String?
    public let expiry: String?
    public let groups: [String]?
    public let cards: [MgmtUserCard]?

    enum CodingKeys: String, CodingKey {
        case id, email, name, role, language, roles, created, modified, expiry, groups, cards
        case authMethods = "auth_methods"
        case idCode = "id_code"
    }
}

// MARK: - Cards (iOS-Tab "Karten", Server v6.7.90+)
//
// Eigenverwaltung der RFID-Karten des angemeldeten Users. Backend-
// Routen: /desktop/cards (Liste), /desktop/cards/profiles (Transforms),
// /desktop/cards/preview (Dry-Run), /desktop/cards (POST: add),
// /desktop/cards/{id} (DELETE).

public struct CardPreview: Codable, Hashable, Sendable {
    public let raw: String
    public let normalized: String
    public let working: String
    public let hex: String
    public let hexReversed: String
    public let decimal: String
    public let decimalReversed: String
    public let base64Text: String
    public let finalSubmitValue: String

    enum CodingKeys: String, CodingKey {
        case raw, normalized, working, hex, decimal
        case hexReversed      = "hex_reversed"
        case decimalReversed  = "decimal_reversed"
        case base64Text       = "base64_text"
        case finalSubmitValue = "final_submit_value"
    }

    public init(raw: String = "", normalized: String = "", working: String = "",
                hex: String = "", hexReversed: String = "",
                decimal: String = "", decimalReversed: String = "",
                base64Text: String = "", finalSubmitValue: String = "") {
        self.raw = raw
        self.normalized = normalized
        self.working = working
        self.hex = hex
        self.hexReversed = hexReversed
        self.decimal = decimal
        self.decimalReversed = decimalReversed
        self.base64Text = base64Text
        self.finalSubmitValue = finalSubmitValue
    }

    // Tolerante Decoder — fehlende/null-Felder werden als "" geparst, damit
    // aeltere DB-Zeilen die iOS-App nicht mit "data couldn't be read" killen.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.raw              = (try? c.decodeIfPresent(String.self, forKey: .raw)) ?? ""
        self.normalized       = (try? c.decodeIfPresent(String.self, forKey: .normalized)) ?? ""
        self.working          = (try? c.decodeIfPresent(String.self, forKey: .working)) ?? ""
        self.hex              = (try? c.decodeIfPresent(String.self, forKey: .hex)) ?? ""
        self.hexReversed      = (try? c.decodeIfPresent(String.self, forKey: .hexReversed)) ?? ""
        self.decimal          = (try? c.decodeIfPresent(String.self, forKey: .decimal)) ?? ""
        self.decimalReversed  = (try? c.decodeIfPresent(String.self, forKey: .decimalReversed)) ?? ""
        self.base64Text       = (try? c.decodeIfPresent(String.self, forKey: .base64Text)) ?? ""
        self.finalSubmitValue = (try? c.decodeIfPresent(String.self, forKey: .finalSubmitValue)) ?? ""
    }
}

public struct Card: Codable, Identifiable, Hashable, Sendable {
    public let id: Int
    public let printixCardId: String
    public let profileId: String
    public let profileName: String
    public let profileVendor: String
    public let profileReaderModel: String
    public let localValue: String
    public let finalValue: String
    public let normalizedValue: String
    public let notes: String
    public let source: String
    public let createdAt: String
    public let updatedAt: String
    public let preview: CardPreview

    enum CodingKeys: String, CodingKey {
        case id, source, notes, preview
        case printixCardId      = "printix_card_id"
        case profileId          = "profile_id"
        case profileName        = "profile_name"
        case profileVendor      = "profile_vendor"
        case profileReaderModel = "profile_reader_model"
        case localValue         = "local_value"
        case finalValue         = "final_value"
        case normalizedValue    = "normalized_value"
        case createdAt          = "created_at"
        case updatedAt          = "updated_at"
    }

    // Tolerante Decoder-Implementierung. Nur `id` ist strikt erforderlich
    // (sollte nie NULL sein — PK in SQLite). Alles andere wird bei
    // fehlendem/null-Wert auf "" bzw. leeres Preview gesetzt, damit ein
    // einzelner DB-Eintrag mit Altdaten nicht die gesamte Cards-Liste
    // unbrauchbar macht.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.id                 = (try? c.decodeIfPresent(Int.self, forKey: .id)) ?? 0
        self.printixCardId      = (try? c.decodeIfPresent(String.self, forKey: .printixCardId)) ?? ""
        self.profileId          = (try? c.decodeIfPresent(String.self, forKey: .profileId)) ?? ""
        self.profileName        = (try? c.decodeIfPresent(String.self, forKey: .profileName)) ?? ""
        self.profileVendor      = (try? c.decodeIfPresent(String.self, forKey: .profileVendor)) ?? ""
        self.profileReaderModel = (try? c.decodeIfPresent(String.self, forKey: .profileReaderModel)) ?? ""
        self.localValue         = (try? c.decodeIfPresent(String.self, forKey: .localValue)) ?? ""
        self.finalValue         = (try? c.decodeIfPresent(String.self, forKey: .finalValue)) ?? ""
        self.normalizedValue    = (try? c.decodeIfPresent(String.self, forKey: .normalizedValue)) ?? ""
        self.notes              = (try? c.decodeIfPresent(String.self, forKey: .notes)) ?? ""
        self.source             = (try? c.decodeIfPresent(String.self, forKey: .source)) ?? ""
        self.createdAt          = (try? c.decodeIfPresent(String.self, forKey: .createdAt)) ?? ""
        self.updatedAt          = (try? c.decodeIfPresent(String.self, forKey: .updatedAt)) ?? ""
        self.preview            = (try? c.decodeIfPresent(CardPreview.self, forKey: .preview)) ?? CardPreview()
    }
}

public struct CardsResponse: Codable, Sendable {
    public let cards: [Card]
}

public struct CardProfile: Codable, Identifiable, Hashable, Sendable {
    public let id: String
    public let name: String
    public let vendor: String
    public let readerModel: String
    public let mode: String
    public let description: String
    public let isBuiltin: Bool

    enum CodingKeys: String, CodingKey {
        case id, name, vendor, mode, description
        case readerModel = "reader_model"
        case isBuiltin   = "is_builtin"
    }
}

public struct CardProfilesResponse: Codable, Sendable {
    public let profiles: [CardProfile]
    /// Firmen-Default den der Admin im Web-Portal gesetzt hat. Leer wenn
    /// keiner gesetzt ist — dann soll der Client den Picker zeigen.
    /// Wenn gesetzt, versteckt die iOS-App den Picker und nutzt still
    /// dieses Profil.
    public let defaultProfileId: String?

    private enum CodingKeys: String, CodingKey {
        case profiles
        case defaultProfileId = "default_profile_id"
    }
}

public struct CardPreviewResponse: Codable, Sendable {
    public let preview: CardPreview
}

public struct CardCreateResponse: Codable, Sendable {
    public let card: Card
}


public struct VersionResponse: Codable, Sendable {
    public let latest: String?
    public let downloadUrlX64: String?
    public let downloadUrlArm64: String?
    public let downloadUrlMac: String?

    enum CodingKeys: String, CodingKey {
        case latest
        case downloadUrlX64   = "download_url_x64"
        case downloadUrlArm64 = "download_url_arm64"
        case downloadUrlMac   = "download_url_mac"
    }
}

// MARK: - Delegate Groups (v4.0)

public struct DelegateGroupMember: Codable, Identifiable, Sendable {
    public let member_email: String
    public let member_display_name: String
    public let member_printix_id: String

    public var id: String { member_email }

    public var displayName: String {
        member_display_name.isEmpty ? member_email : member_display_name
    }

    public init(member_email: String, member_display_name: String, member_printix_id: String) {
        self.member_email = member_email
        self.member_display_name = member_display_name
        self.member_printix_id = member_printix_id
    }
}

public struct DelegateGroup: Codable, Identifiable, Sendable {
    public let group_uuid: String
    public let name: String
    public let created_at: String
    public let members: [DelegateGroupMember]

    public var id: String { group_uuid }

    public init(group_uuid: String, name: String, created_at: String, members: [DelegateGroupMember]) {
        self.group_uuid = group_uuid
        self.name = name
        self.created_at = created_at
        self.members = members
    }
}

public struct DelegateGroupsResponse: Codable, Sendable {
    public let groups: [DelegateGroup]
}

public struct JobStatusResponse: Codable, Sendable {
    public let jobId: String
    public let status: String
    public let printixStatus: String?
    public let fresh: Bool

    enum CodingKeys: String, CodingKey {
        case jobId = "job_id"
        case status
        case printixStatus = "printix_status"
        case fresh
    }
}
