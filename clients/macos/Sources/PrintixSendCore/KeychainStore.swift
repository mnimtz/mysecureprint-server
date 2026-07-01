import Foundation
import Security

// macOS-Keychain-Wrapper — das Pendant zu DPAPI unter Windows.
// Speichert den Bearer-Token unter einem Service-Key, sodass nur der
// angemeldete User ihn entschlüsseln kann. Keine separaten
// Auth-Prompts bei normalem Zugriff (synchronizable=false).

public enum KeychainError: Error {
    case unexpectedStatus(OSStatus)
    case encoding
}

public struct KeychainStore {
    public let service: String
    public let account: String

    public init(service: String = "de.printix.send",
                account: String = "bearer-token") {
        self.service = service
        self.account = account
    }

    // MARK: - Save

    public func set(_ token: String) throws {
        guard let data = token.data(using: .utf8) else { throw KeychainError.encoding }
        try setData(data)
    }

    public func setData(_ data: Data) throws {
        let query: [String: Any] = [
            kSecClass as String:       kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)

        var attrs = query
        attrs[kSecValueData as String]      = data
        attrs[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        let status = SecItemAdd(attrs as CFDictionary, nil)
        guard status == errSecSuccess else { throw KeychainError.unexpectedStatus(status) }
    }

    // MARK: - Load

    public func get() -> String? {
        guard let data = getData() else { return nil }
        return String(data: data, encoding: .utf8)
    }

    public func getData() -> Data? {
        let query: [String: Any] = [
            kSecClass as String:       kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String:  true,
            kSecMatchLimit as String:  kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        guard status == errSecSuccess, let data = item as? Data else { return nil }
        return data
    }

    // MARK: - Delete

    public func clear() {
        let query: [String: Any] = [
            kSecClass as String:       kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
    }
}
