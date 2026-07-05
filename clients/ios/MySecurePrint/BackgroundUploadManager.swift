import Foundation
import ActivityKit
import Combine
import UIKit

/// Hintergrund-Upload via URLSession background configuration.
/// Unterstützt beliebig viele gleichzeitige Uploads (Multi-Target),
/// startet eine Live Activity pro Batch und liefert den Status über
/// die Dynamic Island — auch wenn die App im Hintergrund ist.
///
/// AppDelegate muss `handleBackgroundEvents(identifier:completionHandler:)`
/// aus `application(_:handleEventsForBackgroundURLSession:completionHandler:)`
/// aufrufen, damit iOS die Completion-Events nach einem App-Neustart
/// korrekt zustellt.
@MainActor
final class BackgroundUploadManager: NSObject, ObservableObject {

    static let shared = BackgroundUploadManager()
    static let sessionID  = "de.nimtz.mysecureprint.bgupload"
    static let appGroupID = "group.de.nimtz.mysecureprint"

    // Anzahl laufender Uploads — für das Upload-Tab-Badge
    @Published var pendingCount: Int = 0

    private var taskResponseData: [Int: Data]    = [:]
    private var taskMeta: [Int: UploadTaskMeta]  = [:]
    private var batchActivities: [String: Any]   = [:]  // batchID → Activity<…>
    private var backgroundCompletion: (() -> Void)?

    private struct UploadTaskMeta {
        let batchID: String
        let targetDisplay: String
        let filename: String
        let tempFile: URL
    }

    // Lazy — erst beim ersten Zugriff (oder via handleBackgroundEvents) erstellt,
    // damit iOS die Session nach einem Background-Wake korrekt verknüpfen kann.
    private lazy var session: URLSession = {
        let cfg = URLSessionConfiguration.background(withIdentifier: Self.sessionID)
        cfg.isDiscretionary   = false   // sofort senden, nicht auf WLAN-Opportunität warten
        cfg.sessionSendsLaunchEvents = true
        cfg.httpAdditionalHeaders = ["User-Agent": "MySecurePrint-iOS/1.0"]
        return URLSession(configuration: cfg, delegate: self, delegateQueue: nil)
    }()

    // ── Public API ─────────────────────────────────────────────────────────────

    /// Queued einen Upload-Batch (1..n Ziele). Kehrt sofort zurück.
    func enqueue(
        fileData: Data,
        filename: String,
        targets: [(id: String, display: String)],
        serverURL: String,
        token: String,
        comment: String?,
        copies: Int,
        color: Bool,
        duplex: Bool,
        printImageSize: String,
        groupLabel: String?
    ) {
        guard !targets.isEmpty else { return }

        let batchID = UUID().uuidString
        let firstDisplay = targets.first?.display ?? ""

        if #available(iOS 16.2, *) {
            startActivity(batchID: batchID, filename: filename,
                          targetDisplay: firstDisplay, count: targets.count)
        }

        for (targetId, display) in targets {
            scheduleTask(
                batchID: batchID,
                fileData: fileData,
                filename: filename,
                targetId: targetId,
                targetDisplay: display,
                serverURL: serverURL,
                token: token,
                comment: comment,
                copies: copies,
                color: color,
                duplex: duplex,
                printImageSize: printImageSize,
                groupLabel: groupLabel
            )
        }
        pendingCount += targets.count
    }

    /// Direkter Foreground-Upload — blockiert bis zur Antwort des Servers.
    /// Wirft bei HTTP-Fehler oder Netzwerkfehler. Kein Live-Activity.
    func sendForeground(
        fileData: Data,
        filename: String,
        targets: [(id: String, display: String)],
        serverURL: String,
        token: String,
        comment: String?,
        copies: Int,
        color: Bool,
        duplex: Bool,
        printImageSize: String,
        groupLabel: String?
    ) async throws {
        guard !targets.isEmpty else { return }
        let trimmed = serverURL.trimmingCharacters(in: .init(charactersIn: "/"))
        guard let base = URL(string: trimmed) else {
            throw URLError(.badURL)
        }
        let uploadURL = base.appendingPathComponent("desktop/send")
        for (targetId, _) in targets {
            let boundary = "Boundary-\(UUID().uuidString)"
            let bodyData = buildMultipart(
                boundary: boundary, fileData: fileData, filename: filename,
                targetId: targetId, comment: comment, copies: copies,
                color: color, duplex: duplex, printImageSize: printImageSize,
                groupLabel: groupLabel
            )
            var req = URLRequest(url: uploadURL)
            req.httpMethod = "POST"
            req.setValue("multipart/form-data; boundary=\(boundary)",
                         forHTTPHeaderField: "Content-Type")
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            req.timeoutInterval = 180
            let (_, response) = try await URLSession.shared.upload(for: req, from: bodyData)
            if let http = response as? HTTPURLResponse,
               !(200..<300).contains(http.statusCode) {
                throw URLError(.badServerResponse)
            }
        }
    }

    /// Speichert den Completion-Handler den iOS nach Background-Wake liefert.
    /// Muss SOFORT aus handleEventsForBackgroundURLSession aufgerufen werden.
    func handleBackgroundEvents(identifier: String, completionHandler: @escaping () -> Void) {
        guard identifier == Self.sessionID else { return }
        backgroundCompletion = completionHandler
        _ = session  // Session jetzt initialisieren damit Events ankommen
    }

    // ── Private Helpers ────────────────────────────────────────────────────────

    private func scheduleTask(
        batchID: String,
        fileData: Data,
        filename: String,
        targetId: String,
        targetDisplay: String,
        serverURL: String,
        token: String,
        comment: String?,
        copies: Int,
        color: Bool,
        duplex: Bool,
        printImageSize: String,
        groupLabel: String?
    ) {
        let trimmed = serverURL.trimmingCharacters(in: .init(charactersIn: "/"))
        guard let base = URL(string: trimmed) else { return }
        let uploadURL = base.appendingPathComponent("desktop/send")

        let boundary = "Boundary-\(UUID().uuidString)"
        let bodyData = buildMultipart(
            boundary: boundary, fileData: fileData, filename: filename,
            targetId: targetId, comment: comment, copies: copies,
            color: color, duplex: duplex, printImageSize: printImageSize,
            groupLabel: groupLabel
        )

        // Background-Upload benötigt dateibasierte Tasks
        guard let container = FileManager.default
            .containerURL(forSecurityApplicationGroupIdentifier: Self.appGroupID) else { return }
        let dir = container.appendingPathComponent("uploads", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let tempFile = dir.appendingPathComponent("\(UUID().uuidString).mpfd")
        guard (try? bodyData.write(to: tempFile)) != nil else { return }

        var req = URLRequest(url: uploadURL)
        req.httpMethod = "POST"
        req.setValue("multipart/form-data; boundary=\(boundary)",
                     forHTTPHeaderField: "Content-Type")
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        req.timeoutInterval = 180

        let task = session.uploadTask(with: req, fromFile: tempFile)
        taskMeta[task.taskIdentifier] = UploadTaskMeta(
            batchID: batchID,
            targetDisplay: targetDisplay,
            filename: filename,
            tempFile: tempFile
        )
        task.resume()
    }

    private func buildMultipart(
        boundary: String,
        fileData: Data,
        filename: String,
        targetId: String,
        comment: String?,
        copies: Int,
        color: Bool,
        duplex: Bool,
        printImageSize: String,
        groupLabel: String?
    ) -> Data {
        var body = Data()
        func field(_ name: String, _ value: String) {
            let part = "--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"\r\n\r\n\(value)\r\n"
            body.append(Data(part.utf8))
        }
        field("target_id",        targetId)
        field("copies",           "\(copies)")
        field("color",            color  ? "1" : "")
        field("duplex",           duplex ? "1" : "")
        field("print_image_size", printImageSize)
        if let c = comment, !c.isEmpty { field("comment",     c) }
        if let g = groupLabel, !g.isEmpty { field("group_label", g) }

        let ext  = (filename as NSString).pathExtension
        let mime = guessMime(ext)
        let fileHeader = "--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\nContent-Type: \(mime)\r\n\r\n"
        body.append(Data(fileHeader.utf8))
        body.append(fileData)
        body.append(Data("\r\n--\(boundary)--\r\n".utf8))
        return body
    }

    private func guessMime(_ ext: String) -> String {
        switch ext.lowercased() {
        case "pdf":  return "application/pdf"
        case "png":  return "image/png"
        case "jpg", "jpeg": return "image/jpeg"
        case "heic", "heif": return "image/heic"
        case "docx": return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        case "xlsx": return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        default:     return "application/octet-stream"
        }
    }

    // ── Live Activity ──────────────────────────────────────────────────────────

    @available(iOS 16.2, *)
    private func startActivity(batchID: String, filename: String,
                               targetDisplay: String, count: Int) {
        let info = ActivityAuthorizationInfo()
        print("[LiveActivity] areActivitiesEnabled=\(info.areActivitiesEnabled)")
        guard info.areActivitiesEnabled else {
            print("[LiveActivity] Activities disabled — skipping")
            return
        }
        let attrs = PrintUploadAttributes(filename: filename, targetCount: count)
        let state = PrintUploadAttributes.ContentState(
            phase: .uploading, targetDisplay: targetDisplay)
        do {
            let activity = try Activity.request(
                attributes: attrs,
                content: .init(state: state, staleDate: nil),
                pushType: nil
            )
            batchActivities[batchID] = activity
            print("[LiveActivity] Started: \(activity.id)")
        } catch {
            print("[LiveActivity] Error starting activity: \(error)")
        }
    }

    @available(iOS 16.2, *)
    private func endActivity(batchID: String,
                             finalState: PrintUploadAttributes.ContentState) {
        guard let any = batchActivities.removeValue(forKey: batchID),
              let activity = any as? Activity<PrintUploadAttributes> else { return }
        Task {
            await activity.end(
                .init(state: finalState, staleDate: nil),
                dismissalPolicy: .after(.now + 5)
            )
        }
    }

    // Schreibt einen optimistischen PrintJob in die App-Group,
    // damit ContentView ihn beim nächsten Foreground-Wechsel einliest.
    private func persistPendingJob(filename: String, status: String,
                                   targetDisplay: String, jobId: String) {
        guard let defaults = UserDefaults(suiteName: Self.appGroupID) else { return }
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let job = PrintJob(
            job_id: jobId,
            filename: filename,
            status: status,
            queue: targetDisplay,
            created_at: iso.string(from: Date())
        )
        if let encoded = try? JSONEncoder().encode(job) {
            defaults.set(encoded, forKey: "bgPendingJob_\(jobId)")
        }
    }
}

// ── URLSession Delegate ────────────────────────────────────────────────────────

extension BackgroundUploadManager: URLSessionDataDelegate, URLSessionTaskDelegate {

    // Antwortdaten akkumulieren (JSON von /desktop/send)
    nonisolated func urlSession(_ session: URLSession,
                                dataTask: URLSessionDataTask,
                                didReceive data: Data) {
        let id = dataTask.taskIdentifier
        Task { @MainActor in
            if taskResponseData[id] != nil {
                taskResponseData[id]!.append(data)
            } else {
                taskResponseData[id] = data
            }
        }
    }

    // Task abgeschlossen — Ergebnis auswerten, Activity beenden
    nonisolated func urlSession(_ session: URLSession,
                                task: URLSessionTask,
                                didCompleteWithError error: Error?) {
        let taskID = task.taskIdentifier
        let httpStatus = (task.response as? HTTPURLResponse)?.statusCode

        Task { @MainActor in
            let meta         = taskMeta.removeValue(forKey: taskID)
            let responseData = taskResponseData.removeValue(forKey: taskID)
            pendingCount     = max(0, pendingCount - 1)

            if let tempFile = meta?.tempFile {
                try? FileManager.default.removeItem(at: tempFile)
            }
            guard let meta else { return }

            let isSuccess = error == nil && httpStatus.map { (200..<300).contains($0) } == true

            if isSuccess {
                // Optimistischen Job in App-Group schreiben
                struct Partial: Decodable { let jobId: String?; let status: String? }
                let parsed = responseData.flatMap { try? JSONDecoder().decode(Partial.self, from: $0) }
                let jobId  = parsed?.jobId ?? UUID().uuidString
                let status = parsed?.status ?? "queued"
                persistPendingJob(filename: meta.filename, status: status,
                                  targetDisplay: meta.targetDisplay, jobId: jobId)

                if #available(iOS 16.2, *) {
                    endActivity(batchID: meta.batchID,
                                finalState: .init(phase: .sent,
                                                  targetDisplay: meta.targetDisplay))
                }
            } else {
                let reason = error?.localizedDescription
                    ?? "HTTP \(httpStatus ?? 0)"
                if #available(iOS 16.2, *) {
                    endActivity(batchID: meta.batchID,
                                finalState: .init(phase: .failed,
                                                  targetDisplay: meta.targetDisplay,
                                                  errorMessage: reason))
                }
                // Fehler für UploadView sichtbar machen
                if let defaults = UserDefaults(suiteName: Self.appGroupID) {
                    defaults.set(["message": reason], forKey: "lastShareError")
                }
            }
        }
    }

    // iOS ruft das auf nachdem alle Events nach einem Background-Wake zugestellt wurden
    nonisolated func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        Task { @MainActor in
            backgroundCompletion?()
            backgroundCompletion = nil
        }
    }
}
