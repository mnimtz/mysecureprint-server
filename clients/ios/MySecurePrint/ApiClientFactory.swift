import Foundation
import PrintixSendCore

/// Zentrale Fabrik-Funktion für den ApiClient.
///
/// Wir erzeugen bewusst pro Flow einen frischen Client (Login, Upload,
/// Targets), statt einen Singleton zu halten — URLSession-Pooling sorgt
/// intern ohnehin für Connection-Reuse, und wir vermeiden stale-Token-
/// Probleme zwischen Login/Logout-Wechseln.
enum ApiClientFactory {
    static func make(baseURL: String, token: String?) -> PrintixSendCore.ApiClient? {
        try? PrintixSendCore.ApiClient(baseUrl: baseURL, token: token)
    }
}
