import Foundation
import Combine
import PrintixSendCore

/// Zentraler In-Memory-Cache für alle Tab-Daten.
///
/// Wird beim App-Start / Login einmal im Hintergrund befüllt (preloadIfNeeded)
/// und danach von allen Tabs sofort ohne Wartezeit angezeigt. Pull-to-Refresh
/// oder der Sync-Button rufen refresh() auf und aktualisieren den Cache live.
@MainActor
final class AppCache: ObservableObject {

    static let jobPageSize = 30

    // ── Gecachte Daten ────────────────────────────────────────────────────
    @Published var targets: [Target] = []
    @Published var userCanChoose: Bool = false
    @Published var queues: [QueueItem] = []
    @Published var jobs: [PrintJob] = []
    @Published var jobsHasMore: Bool = false

    // ── Sync-Status ───────────────────────────────────────────────────────
    @Published var isSyncing: Bool = false
    @Published var lastSyncedAt: Date? = nil
    @Published var syncError: String = ""

    private var preloaded = false

    // ── Öffentliche API ───────────────────────────────────────────────────

    /// Beim App-Start aufrufen — lädt nur wenn Cache noch leer ist.
    func preloadIfNeeded(settings: SettingsStore) async {
        guard !preloaded else { return }
        await sync(settings: settings, showSpinner: false)
    }

    /// Manueller Sync (Sync-Knopf oder Pull-to-Refresh).
    func refresh(settings: SettingsStore) async {
        await sync(settings: settings, showSpinner: true)
    }

    /// Cache bei Logout leeren.
    func invalidate() {
        targets = []
        queues = []
        jobs = []
        jobsHasMore = false
        preloaded = false
        isSyncing = false
        syncError = ""
        lastSyncedAt = nil
    }

    // ── Interne Sync-Logik ────────────────────────────────────────────────

    private func sync(settings: SettingsStore, showSpinner: Bool) async {
        guard let client = ApiClientFactory.make(
            baseURL: settings.serverURL, token: settings.bearerToken) else { return }
        if showSpinner { isSyncing = true }
        syncError = ""
        defer { isSyncing = false }

        // Alle drei Quellen parallel holen — wall-clock = langsamster Einzelaufruf
        async let tResult = fetchTargets(client: client, settings: settings)
        async let qResult = fetchQueues(client: client)
        async let jResult = fetchJobs(client: client)

        let (t, q, j) = await (tResult, qResult, jResult)

        if let r = t {
            targets = r.items
            userCanChoose = r.canChoose
            applyTargetLabels(r.items, settings: settings)
        }
        if let newQueues = q { queues = newQueues }
        if let r = j { jobs = r.items; jobsHasMore = r.hasMore }

        preloaded = true
        lastSyncedAt = Date()
    }

    // ── Fetch-Helfer ──────────────────────────────────────────────────────

    private struct TargetResult { let items: [Target]; let canChoose: Bool }
    private struct JobResult    { let items: [PrintJob]; let hasMore: Bool }

    private func fetchTargets(client: ApiClient,
                               settings: SettingsStore) async -> TargetResult? {
        do {
            let resp = try await client.targetsFull()
            let all  = resp.targets
            let visible = settings.delegateEnabled
                ? all
                : all.filter { $0.type != "print_delegate" }
            return TargetResult(items: visible, canChoose: resp.userCanChoose ?? false)
        } catch { return nil }
    }

    private func fetchQueues(client: ApiClient) async -> [QueueItem]? {
        do {
            let resp = try await client.listQueues()
            return resp.queues
        } catch { return nil }
    }

    private func fetchJobs(client: ApiClient) async -> JobResult? {
        do {
            let data = try await client.myJobs(limit: Self.jobPageSize, offset: 0)
            let resp = try JSONDecoder().decode(JobsResponse.self, from: data)
            return JobResult(items: resp.jobs, hasMore: resp.jobs.count >= Self.jobPageSize)
        } catch { return nil }
    }

    private func applyTargetLabels(_ items: [Target], settings: SettingsStore) {
        var labels = settings.targetLabels.filter {
            $0.key.hasPrefix("print:queue:") || $0.key.hasPrefix("print:user:")
        }
        for t in items {
            let label = t.label.trimmingCharacters(in: .whitespaces)
            if !label.isEmpty { labels[t.id] = label }
        }
        settings.targetLabels = labels
        // Default-Queue setzen falls noch nichts ausgewählt
        if settings.selectedTargetIds.isEmpty, let first = items.first {
            settings.selectedTargetIds = [first.id]
        }
        // Ungültige IDs aus Auswahl prunen (außer print:queue/user — die kommen
        // aus dem Picker und sind nicht in targets enthalten)
        let allowed = Set(items.map { $0.id })
        let pruned  = settings.selectedTargetIds.filter {
            allowed.contains($0)
                || $0.hasPrefix("print:queue:")
                || $0.hasPrefix("print:user:")
        }
        if pruned != settings.selectedTargetIds {
            settings.selectedTargetIds = pruned
        }
    }
}
