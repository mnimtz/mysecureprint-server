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

    // Management (nur Admins/Users — wird nur befüllt wenn hasManagementAccess)
    @Published var mgmtStats: MgmtStatsResponse? = nil
    @Published var mgmtPrinters: [MgmtPrinter] = []
    @Published var mgmtUsers: [MgmtUser] = []
    @Published var mgmtWorkstations: [MgmtWorkstation] = []
    @Published var mgmtLastSyncedAt: Date? = nil

    // Delegate-Teams (Phase F, v4.0)
    @Published var delegateGroups: [DelegateGroup] = []

    // Optimistic Insert: nach erfolgreichem Job-Submit aus UploadView setzen,
    // damit JobsView den Job sofort oben anzeigt ohne Full-Refresh.
    @Published var pendingJob: PrintJob? = nil

    // ── Sync-Status ───────────────────────────────────────────────────────
    @Published var isSyncing: Bool = false
    @Published var isInitialLoad: Bool = true   // true bis erster Fetch abgeschlossen
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

    /// Nur die Jobs-Liste neu laden — für den Post-Submit-Refresh.
    /// Kein Full-Sync, kein Spinner, kein Fluten. Einmal nach Abschluss
    /// aller Sends aufrufen, damit echte Daten + has_preview ankommen.
    func refreshJobs(settings: SettingsStore) async {
        guard let client = ApiClientFactory.make(
            baseURL: settings.serverURL, token: settings.bearerToken) else { return }
        if let r = await fetchJobs(client: client) {
            jobs = r.items
            jobsHasMore = r.hasMore
            pendingJob = nil   // Optimistic-Eintrag ist jetzt von echten Daten abgelöst
        }
        triggerStatusRefresh(client: client)
    }

    /// Feuert Hintergrund-Status-Abfragen für nicht-terminale Jobs ab (max 8).
    /// Aktualisiert `jobs` sobald Printix eine neue State zurückgibt.
    private func triggerStatusRefresh(client: ApiClient) {
        let stale = jobs.filter { !PrintJob.isTerminal($0.status) }.prefix(8).map { $0.job_id }
        guard !stale.isEmpty else { return }
        Task {
            for jobId in stale {
                guard let r = try? await client.jobStatus(jobId: jobId) else { continue }
                if let idx = jobs.firstIndex(where: { $0.job_id == jobId }),
                   r.status != jobs[idx].status {
                    jobs[idx] = jobs[idx].withUpdatedStatus(r.status)
                }
            }
        }
    }

    /// Cache bei Logout leeren.
    func invalidate() {
        targets = []
        queues = []
        jobs = []
        jobsHasMore = false
        mgmtStats = nil
        mgmtPrinters = []
        mgmtUsers = []
        mgmtWorkstations = []
        mgmtLastSyncedAt = nil
        delegateGroups = []
        pendingJob = nil
        preloaded = false
        isInitialLoad = true
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

        // Alle Quellen parallel holen — wall-clock = langsamster Einzelaufruf
        async let tResult = fetchTargets(client: client, settings: settings)
        async let qResult = fetchQueues(client: client)
        async let jResult = fetchJobs(client: client)
        async let mResult = settings.hasManagementAccess
            ? fetchManagement(client: client)
            : nil
        async let gResult = settings.delegateEnabled
            ? fetchDelegateGroups(client: client)
            : []

        let (t, q, j, m, g) = await (tResult, qResult, jResult, mResult, gResult)

        if let r = t {
            targets = r.items
            userCanChoose = r.canChoose
            applyTargetLabels(r.items, settings: settings)
        }
        if let newQueues = q { queues = newQueues }
        if let r = j { jobs = r.items; jobsHasMore = r.hasMore; triggerStatusRefresh(client: client) }
        if let r = m {
            mgmtStats         = r.stats
            mgmtPrinters      = r.printers
            mgmtUsers         = r.users
            mgmtWorkstations  = r.workstations
            mgmtLastSyncedAt  = Date()
        }
        delegateGroups = g ?? []

        preloaded = true
        isInitialLoad = false
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

    private struct MgmtResult {
        let stats: MgmtStatsResponse?
        let printers: [MgmtPrinter]
        let users: [MgmtUser]
        let workstations: [MgmtWorkstation]
    }

    private func fetchManagement(client: ApiClient) async -> MgmtResult {
        async let s = try? client.managementStats()
        async let p = try? client.managementPrinters()
        async let u = try? client.managementUsers()
        async let w = try? client.managementWorkstations()
        let (stats, printers, users, wkst) = await (s, p, u, w)
        return MgmtResult(
            stats:        stats,
            printers:     printers?.printers ?? [],
            users:        users?.users ?? [],
            workstations: wkst?.workstations ?? []
        )
    }

    private func fetchDelegateGroups(client: ApiClient) async -> [DelegateGroup]? {
        do {
            return try await client.listDelegateGroups()
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
        // Default-Queue setzen falls noch nichts ausgewählt.
        // Vorzug: print:self (eigene SecurePrint-Queue) — das ist das
        // konzeptuelle Default des Apps. Fallback: erster Server-Eintrag.
        if settings.selectedTargetIds.isEmpty {
            let preferred = items.first(where: { $0.id == SettingsStore.defaultTargetId })
                         ?? items.first
            if let id = preferred?.id {
                settings.selectedTargetIds = [id]
            }
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
