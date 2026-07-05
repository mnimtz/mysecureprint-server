import ActivityKit
import Foundation

// Dieses File muss in BEIDEN Targets sein:
// • MySecurePrint (Haupt-App — startet/updated die Activity)
// • PrintUploadWidget (Widget Extension — rendert die Dynamic Island Views)

struct PrintUploadAttributes: ActivityAttributes {

    /// Statische Infos pro Upload-Batch
    var filename: String
    var targetCount: Int

    /// Dynamischer Zustand der Activity
    struct ContentState: Codable, Hashable {
        enum Phase: String, Codable {
            case uploading  // läuft gerade
            case sent       // erfolgreich gesendet
            case failed     // Fehler
        }
        var phase: Phase
        var targetDisplay: String      // Queue- oder User-Name
        var errorMessage: String?      // nur bei .failed
    }
}
