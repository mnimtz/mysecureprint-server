import Foundation
import Security

/// Keychain-Wrapper fuer den Bearer-Token (C-4).
///
/// Liegt in einer geteilten Keychain-Access-Group, damit Haupt-App
/// und Share-Extension denselben Token sehen. Im Vergleich zu
/// UserDefaults:
///   - nicht in iTunes/Finder-Backups enthalten (AfterFirstUnlockThisDeviceOnly),
///   - Sandbox-/Jailbreak-resistenter,
///   - ueberlebt App-Loeschung (kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
///     + Keychain-Access-Group bleiben nach App-Loeschung erhalten).
///
/// Service-Name und Access-Group muessen mit den Entitlements
/// uebereinstimmen.
enum KeychainTokenStore {

    static let service     = "de.nimtz.mysecureprint"
    static let accessGroup = "group.de.nimtz.mysecureprint"  // ohne $(AppIdentifierPrefix) — iOS prependet das selbst
    static let account     = "bearerToken"

    /// Schreibt (oder ueberschreibt) den Token. Leerstring -> delete().
    @discardableResult
    static func set(_ token: String) -> Bool {
        guard !token.isEmpty else { return delete() }

        // Erst loeschen, dann adden — vermeidet die "errSecDuplicateItem"-
        // Codepath, ist atomar genug fuer unseren Single-User-Flow.
        _ = delete()

        guard let data = token.data(using: .utf8) else { return false }

        let query: [String: Any] = [
            kSecClass as String:            kSecClassGenericPassword,
            kSecAttrService as String:      service,
            kSecAttrAccount as String:      account,
            kSecAttrAccessGroup as String:  accessGroup,
            kSecAttrAccessible as String:   kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
            kSecValueData as String:        data,
        ]
        let status = SecItemAdd(query as CFDictionary, nil)
        return status == errSecSuccess
    }

    /// Liest den aktuellen Token. Leerer String wenn nichts gespeichert.
    static func get() -> String {
        let query: [String: Any] = [
            kSecClass as String:            kSecClassGenericPassword,
            kSecAttrService as String:      service,
            kSecAttrAccount as String:      account,
            kSecAttrAccessGroup as String:  accessGroup,
            kSecMatchLimit as String:       kSecMatchLimitOne,
            kSecReturnData as String:       true,
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        guard status == errSecSuccess,
              let data = item as? Data,
              let str = String(data: data, encoding: .utf8) else {
            return ""
        }
        return str
    }

    /// Loescht den Token. Idempotent.
    @discardableResult
    static func delete() -> Bool {
        let query: [String: Any] = [
            kSecClass as String:            kSecClassGenericPassword,
            kSecAttrService as String:      service,
            kSecAttrAccount as String:      account,
            kSecAttrAccessGroup as String:  accessGroup,
        ]
        let status = SecItemDelete(query as CFDictionary)
        return status == errSecSuccess || status == errSecItemNotFound
    }
}
