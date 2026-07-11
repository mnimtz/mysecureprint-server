import Foundation
import Combine
import WidgetKit
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

    /// Nur die Jobs-Liste neu laden — für den Post-Submit-Refresh oder
    /// manuellen Sync. noCache=true umgeht den Server-Cache (30s TTL),
    /// damit KI-Flags + queue-Name sofort sichtbar sind.
    /// pollStatusNow=true: einmaliger sofortiger Status-Poll nach dem Laden
    /// (sinnvoll bei manuellem Refresh, damit ältere Jobs sofort live-Status zeigen).
    func refreshJobs(settings: SettingsStore, noCache: Bool = false,
                     pollStatusNow: Bool = false) async {
        guard let client = ApiClientFactory.make(
            baseURL: settings.serverURL, token: settings.bearerToken) else { return }
        if let r = await fetchJobs(client: client, noCache: noCache) {
            // Pending nur löschen wenn der echte Job schon in der Server-Liste ist.
            // Printix braucht 30–60s zum Indexieren — zu früh gecleart führt dazu
            // dass der Platzhalter aus der UI verschwindet bevor der echte Eintrag da ist.
            let pendingFoundInList = pendingJob.map { p in
                r.items.contains(where: { $0.job_id == p.job_id || $0.filename == p.filename })
            } ?? true
            jobs = r.items
            jobsHasMore = r.hasMore
            if pendingFoundInList { pendingJob = nil }
            updateWidgetState(jobs: r.items)
        }
        // Bei manuellem Refresh: einmaligen Status-Poll starten damit nicht auf
        // das nächste adaptive Intervall gewartet werden muss (könnte 5–30 min sein).
        // Das ist KEIN Loop — erzeugt keine parallele Poll-Instanz.
        if pollStatusNow && hasNonTerminalJobs {
            await pollNonTerminalJobsWithClient(client)
        }
    }

    /// Schreibt den aktuellen Job-Zustand ins App Group und weist WidgetKit
    /// an, die Lock Screen Widget Timeline sofort neu zu rendern.
    func updateWidgetState(jobs: [PrintJob]) {
        // v1.6.1: PrintJob.isTerminal statt hardcoded status-Liste, damit
        // widget-Badge und Poll-Loop nicht drift wenn ein neuer non-terminal
        // status auftaucht.
        let pending = jobs.filter { !PrintJob.isTerminal($0.status) }.count
        let last = jobs.first
        let state = WidgetJobState(
            pendingCount: pending,
            lastFilename: last?.filename,
            lastStatus: last?.status,
            lastQueue: last?.queue,
            updatedAt: Date()
        )
        state.save(appGroupID: SettingsStore.appGroupID)
        WidgetCenter.shared.reloadTimelines(ofKind: "PrintJobStatusWidget")
    }

    /// Gibt true zurück wenn noch nicht-terminale Jobs im Cache sind.
    var hasNonTerminalJobs: Bool {
        jobs.contains { !PrintJob.isTerminal($0.status) }
    }

    /// Adaptives Poll-Intervall basierend auf dem Alter des jüngsten nicht-terminalen Jobs.
    /// Anywhere-Queue-Jobs können Stunden am Drucker warten — seltener pollen spart API-Calls.
    var nextPollInterval: TimeInterval {
        let nonTerminal = jobs.filter { !PrintJob.isTerminal($0.status) }
        guard !nonTerminal.isEmpty else { return 60 }
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let isoFallback = ISO8601DateFormatter()
        let now = Date()
        let minAge: TimeInterval = nonTerminal.compactMap { job -> TimeInterval? in
            let raw = job.forwarded_at ?? job.created_at
            let date = iso.date(from: raw) ?? isoFallback.date(from: raw)
            return date.map { now.timeIntervalSince($0) }
        }.min() ?? 600
        switch minAge {
        case ..<180:    return 20     // < 3 min: sofortiger Feedback (Sofortdruck/Fehler)
        case ..<600:    return 60     // 3–10 min: Nutzer läuft zum Drucker
        case ..<7200:   return 300    // 10 min–2h: Anywhere-Job wartet auf Release
        case ..<28800:  return 900    // 2h–8h: seltener, Job vielleicht vergessen
        default:        return 1800   // 8h+: alle 30 min bis Printix löscht (~72h)
        }
    }

    /// Fragt für alle nicht-terminalen Jobs (max 8) den Live-Status von Printix ab
    /// und aktualisiert den Cache. Awaitable — für die automatische Poll-Loop.
    func pollNonTerminalJobs(settings: SettingsStore) async {
        guard let client = ApiClientFactory.make(
            baseURL: settings.serverURL, token: settings.bearerToken) else { return }
        await pollNonTerminalJobsWithClient(client)
    }

    private func pollNonTerminalJobsWithClient(_ client: ApiClient) async {
        let stale = jobs.filter { !PrintJob.isTerminal($0.status) }.prefix(8).map { $0.job_id }
        print("[StatusPoll] polling \(stale.count) non-terminal jobs")
        for jobId in stale {
            do {
                let r = try await client.jobStatus(jobId: jobId)
                print("[StatusPoll] job=\(jobId) → \(r.status) (fresh=\(r.fresh))")
                if let idx = jobs.firstIndex(where: { $0.job_id == jobId }),
                   r.status != jobs[idx].status {
                    jobs[idx] = jobs[idx].withUpdatedStatus(r.status)
                    updateWidgetState(jobs: jobs)
                }
            } catch {
                print("[StatusPoll] ERROR job=\(jobId): \(error)")
            }
        }
    }

    private func triggerStatusRefresh(client: ApiClient) {
        Task { await pollNonTerminalJobsWithClient(client) }
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
        if let r = j {
            jobs = r.items
            jobsHasMore = r.hasMore
            updateWidgetState(jobs: r.items)
        }
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

    private func fetchJobs(client: ApiClient, noCache: Bool = false) async -> JobResult? {
        do {
            let data = try await client.myJobs(limit: Self.jobPageSize, offset: 0, noCache: noCache)
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
        // v0.7.224 — Fehler pro Bucket loggen statt still schlucken. So sieht
        // man im Xcode-Log sofort ob z.B. der User-Endpoint auth-Fehler wirft
        // oder ob der Printix-Tenant gar nicht konfiguriert ist.
        async let s = _fetchLogged(name: "managementStats")        { try await client.managementStats() }
        async let p = _fetchLogged(name: "managementPrinters")     { try await client.managementPrinters() }
        async let u = _fetchLogged(name: "managementUsers")        { try await client.managementUsers() }
        async let w = _fetchLogged(name: "managementWorkstations") { try await client.managementWorkstations() }
        let (stats, printers, users, wkst) = await (s, p, u, w)
        return MgmtResult(
            stats:        stats,
            printers:     printers?.printers ?? [],
            users:        users?.users ?? [],
            workstations: wkst?.workstations ?? []
        )
    }

    /// Hilfs-Wrapper: führt fetch aus, gibt nil zurück bei Fehler und loggt.
    private func _fetchLogged<T>(name: String,
                                 fetch: () async throws -> T) async -> T? {
        do {
            return try await fetch()
        } catch {
            print("[AppCache.\(name)] failed: \(error)")
            return nil
        }
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
