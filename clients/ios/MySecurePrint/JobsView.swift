import SwiftUI
import PrintixSendCore

// MARK: - Jobs Tab

struct JobsView: View {

    @EnvironmentObject private var settings: SettingsStore
    @EnvironmentObject private var cache: AppCache

    @State private var jobs: [PrintJob] = []
    @State private var loading = false
    @State private var loadingMore = false
    @State private var error = ""
    @State private var searchText = ""
    @State private var hasMore = false
    @State private var selectedJob: PrintJob? = nil
    @State private var showClearConfirm = false
    @State private var initializedFromCache = false

    private let pageSize = 30

    private var filteredJobs: [PrintJob] {
        guard !searchText.trimmingCharacters(in: .whitespaces).isEmpty else { return jobs }
        let q = searchText.lowercased()
        return jobs.filter {
            $0.filename.lowercased().contains(q)
            || $0.queue.lowercased().contains(q)
            || $0.status.lowercased().contains(q)
            || ($0.delegated_from ?? "").lowercased().contains(q)
            || ($0.hostname ?? "").lowercased().contains(q)
        }
    }

    var body: some View {
        NavigationStack {
            List {
                if loading && jobs.isEmpty {
                    HStack {
                        ProgressView()
                        Text(String(localized: "Lade Jobs …"))
                    }
                } else if filteredJobs.isEmpty && !loading {
                    emptyState
                } else {
                    ForEach(filteredJobs) { job in
                        JobRow(job: job)
                            .contentShape(Rectangle())
                            .onTapGesture { selectedJob = job }
                    }
                    if hasMore && searchText.trimmingCharacters(in: .whitespaces).isEmpty {
                        loadMoreButton
                    }
                }

                if !error.isEmpty {
                    Section(String(localized: "Fehler")) {
                        Text(error).foregroundColor(.red).textSelection(.enabled)
                    }
                }
            }
            .brandNavStyle(title: String(localized: "Meine Jobs"))
            .tint(MSP.cyan)
            .listStyle(.insetGrouped)
            .searchable(text: $searchText, prompt: String(localized: "Jobs durchsuchen …"))
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button {
                        Task { await reload() }
                    } label: {
                        if loading && !jobs.isEmpty {
                            ProgressView().scaleEffect(0.8)
                        } else {
                            Image(systemName: "arrow.clockwise")
                        }
                    }
                    .disabled(loading)
                }
            }
            .refreshable { await reload() }
            .task {
                // Sofort Cache-Daten anzeigen, dann ggf. live nachladen
                if !initializedFromCache && !cache.jobs.isEmpty {
                    jobs = cache.jobs
                    hasMore = cache.jobsHasMore
                    initializedFromCache = true
                } else if jobs.isEmpty {
                    await reload()
                }
            }
            // Optimistic Insert: sobald UploadView einen Job erfolgreich gesendet
            // hat, setzt sie cache.pendingJob. Wir prependen ihn sofort oben in
            // die Liste — ohne Wartezeit / Pull-to-Refresh. Deduplizierung via
            // job_id falls ein Full-Refresh schon stattgefunden hat.
            .onChange(of: cache.pendingJob) { _, newJob in
                guard let job = newJob else { return }
                guard !jobs.contains(where: { $0.job_id == job.job_id }) else { return }
                jobs.insert(job, at: 0)
                cache.jobs = jobs
            }
            .sheet(item: $selectedJob) { job in
                JobDetailView(job: job)
            }
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    if !jobs.isEmpty {
                        Button(role: .destructive) {
                            showClearConfirm = true
                        } label: {
                            Image(systemName: "trash")
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
            .confirmationDialog(
                String(localized: "Job-Verlauf löschen?"),
                isPresented: $showClearConfirm,
                titleVisibility: .visible
            ) {
                Button(String(localized: "Verlauf löschen"), role: .destructive) {
                    Task { await clearHistory() }
                }
            } message: {
                Text(String(localized: "Alle Einträge werden unwiderruflich entfernt."))
            }
        }
    }

    @ViewBuilder
    private var emptyState: some View {
        Section {
            VStack(alignment: .leading, spacing: 6) {
                if searchText.trimmingCharacters(in: .whitespaces).isEmpty {
                    Text(String(localized: "Noch keine Druck-Jobs"))
                        .font(.headline)
                    Text(String(localized: "Hier landen deine Aufträge nach dem Senden — inkl. Status (an Printix gesendet, gedruckt, Fehler)."))
                        .font(.caption)
                        .foregroundColor(.secondary)
                } else {
                    Text(String(localized: "Keine Treffer"))
                        .font(.headline)
                    Text(String(localized: "Versuche einen anderen Suchbegriff."))
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }
            .padding(.vertical, 8)
        }
    }

    @ViewBuilder
    private var loadMoreButton: some View {
        Button {
            Task { await loadMore() }
        } label: {
            HStack {
                if loadingMore {
                    ProgressView().scaleEffect(0.8)
                }
                Text(String(localized: "Mehr laden"))
            }
            .frame(maxWidth: .infinity)
        }
        .disabled(loadingMore)
    }

    // MARK: - Data loading

    @MainActor
    private func reload() async {
        error = ""
        guard !settings.serverURL.isEmpty, !settings.bearerToken.isEmpty else {
            error = String(localized: "Nicht angemeldet.")
            return
        }
        loading = true
        cache.isSyncing = true
        defer { loading = false; cache.isSyncing = false }
        do {
            let result = try await fetchJobs(offset: 0)
            jobs = result.jobs
            hasMore = result.jobs.count >= pageSize
            cache.jobs = result.jobs
            cache.jobsHasMore = hasMore
        } catch {
            self.error = error.localizedDescription
        }
    }

    @MainActor
    private func loadMore() async {
        guard !loadingMore else { return }
        loadingMore = true
        defer { loadingMore = false }
        do {
            let result = try await fetchJobs(offset: jobs.count)
            jobs.append(contentsOf: result.jobs)
            hasMore = result.jobs.count >= pageSize
        } catch {
            self.error = error.localizedDescription
        }
    }

    @MainActor
    private func clearHistory() async {
        guard let client = ApiClientFactory.make(baseURL: settings.serverURL,
                                                  token: settings.bearerToken) else { return }
        _ = try? await client.deleteMyJobs()
        jobs = []
        hasMore = false
    }

    private func fetchJobs(offset: Int) async throws -> JobsResponse {
        guard let client = ApiClientFactory.make(baseURL: settings.serverURL,
                                                 token: settings.bearerToken) else {
            throw ApiError.invalidUrl
        }
        let data = try await client.myJobs(limit: pageSize, offset: offset)
        return try JSONDecoder().decode(JobsResponse.self, from: data)
    }
}

// MARK: - Job Row

private struct JobRow: View {
    let job: PrintJob

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            // Filename + status badge
            HStack(alignment: .top) {
                Text(job.filename.isEmpty ? String(localized: "(ohne Namen)") : job.filename)
                    .font(.body)
                    .lineLimit(1)
                Spacer()
                statusBadge
            }
            // Queue + date
            HStack(spacing: 6) {
                if !job.queue.isEmpty {
                    Label(job.queue, systemImage: "printer.fill")
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                }
                Spacer()
                Text(displayDate)
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            // Delegiert-von-Flag (empfangener Delegate-Job)
            if let delegate = job.delegated_from, !delegate.isEmpty {
                delegateTag(name: delegate)
            }
            // Empfänger-Flag (selbst delegiert)
            if let recipients = job.delegate_recipients, !recipients.isEmpty {
                recipientTag(recipients: recipients, groupName: job.delegate_group_name)
            }
            // Error
            if let err = job.error_message, !err.isEmpty {
                Text(err)
                    .font(.caption2)
                    .foregroundColor(.red)
                    .lineLimit(2)
            }
        }
        .padding(.vertical, 4)
    }

    @ViewBuilder
    private func delegateTag(name: String) -> some View {
        HStack(spacing: 4) {
            Image(systemName: "person.2.fill")
                .font(.caption2)
            Text(name)
                .font(.caption)
                .lineLimit(1)
        }
        .foregroundColor(MSP.cyan)
        .padding(.horizontal, 7)
        .padding(.vertical, 2)
        .background(MSP.cyan.opacity(0.12))
        .clipShape(Capsule())
    }

    @ViewBuilder
    private func recipientTag(recipients: [String], groupName: String?) -> some View {
        let label: String = {
            if let g = groupName, !g.isEmpty {
                return "→ \(g) · \(recipients.count)"
            }
            if recipients.count == 1 {
                let r = recipients[0]
                return "→ \(r)"
            }
            return String(format: String(localized: "→ %d Empfänger"), recipients.count)
        }()
        HStack(spacing: 4) {
            Image(systemName: groupName != nil && !(groupName!.isEmpty) ? "person.3.fill" : "person.fill.badge.plus")
                .font(.caption2)
            Text(label)
                .font(.caption)
                .lineLimit(1)
        }
        .foregroundColor(.orange)
        .padding(.horizontal, 7)
        .padding(.vertical, 2)
        .background(Color.orange.opacity(0.12))
        .clipShape(Capsule())
    }

    private var displayDate: String {
        let raw = job.forwarded_at ?? job.created_at
        return PrintJob.formatDate(raw, style: .short)
    }

    @ViewBuilder
    private var statusBadge: some View {
        let (color, label) = job.badgeStyle
        Text(label)
            .font(.caption)
            .fontWeight(.semibold)
            .padding(.horizontal, 8)
            .padding(.vertical, 2)
            .background(color.opacity(0.15))
            .foregroundColor(color)
            .clipShape(Capsule())
    }
}

// MARK: - Job Detail Sheet

struct JobDetailView: View {
    let job: PrintJob
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var settings: SettingsStore
    @EnvironmentObject private var cache: AppCache
    @State private var previewImage: UIImage? = nil
    @State private var previewLoading = false
    @State private var previewFullscreen = false
    @State private var displayStatus: String = ""

    var body: some View {
        NavigationStack {
            List {
                // Vorschau-Bild: Section nur sichtbar wenn Bild geladen (oder
                // noch lädt). Die eigentliche Lade-Task läuft auf View-Ebene
                // unabhängig von has_preview im gecachten Job-Objekt.
                if previewImage != nil || previewLoading {
                    Section {
                        previewSection
                    }
                }

                // Status
                Section {
                    HStack {
                        Text(String(localized: "Status"))
                            .foregroundColor(.secondary)
                        Spacer()
                        let (color, label) = PrintJob.badgeStyleFor(
                            displayStatus.isEmpty ? job.status : displayStatus)
                        Text(label)
                            .font(.caption)
                            .fontWeight(.semibold)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 2)
                            .background(color.opacity(0.15))
                            .foregroundColor(color)
                            .clipShape(Capsule())
                    }
                }

                // File
                Section(String(localized: "Datei")) {
                    if !job.filename.isEmpty {
                        detailRow(String(localized: "Name"), value: job.filename)
                    }
                    if let size = job.data_size, size > 0 {
                        detailRow(String(localized: "Größe"), value: formatBytes(size))
                    }
                }

                // Target
                Section(String(localized: "Ziel")) {
                    if !job.queue.isEmpty {
                        detailRow(String(localized: "Queue"), value: job.queue, icon: "printer.fill")
                    }
                    if let delegate = job.delegated_from, !delegate.isEmpty {
                        detailRow(String(localized: "Delegiert von"), value: delegate,
                                  icon: "person.2.fill", iconColor: MSP.cyan)
                    }
                    if let recipients = job.delegate_recipients, !recipients.isEmpty {
                        if let g = job.delegate_group_name, !g.isEmpty {
                            detailRow(String(localized: "Team"), value: "\(g) (\(recipients.count))",
                                      icon: "person.3.fill", iconColor: .orange)
                        }
                        ForEach(recipients, id: \.self) { r in
                            detailRow(String(localized: "Empfänger"), value: r,
                                      icon: "person.fill.badge.plus", iconColor: .orange)
                        }
                    }
                    if let src = job.source_identity, !src.isEmpty {
                        detailRow(String(localized: "Absender"), value: src, icon: "person.fill")
                    }
                }

                // Device / timing
                Section(String(localized: "Details")) {
                    if let host = job.hostname, !host.isEmpty {
                        detailRow(String(localized: "Gerät"), value: host, icon: "desktopcomputer")
                    }
                    detailRow(String(localized: "Erstellt"),
                              value: PrintJob.formatDate(job.created_at, style: .medium))
                    if let fwd = job.forwarded_at, !fwd.isEmpty {
                        detailRow(String(localized: "Weitergeleitet"),
                                  value: PrintJob.formatDate(fwd, style: .medium))
                    }
                    detailRow(String(localized: "Job-ID"), value: job.job_id)
                }

                if let err = job.error_message, !err.isEmpty {
                    Section(String(localized: "Fehler")) {
                        Text(err)
                            .foregroundColor(.red)
                            .font(.caption)
                            .textSelection(.enabled)
                    }
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle(job.filename.isEmpty ? String(localized: "Job-Details") : job.filename)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button(String(localized: "Fertig")) { dismiss() }
                }
            }
            // Preview immer laden + Status pollen solange nicht terminal.
            .task {
                displayStatus = job.status
                guard let client = ApiClientFactory.make(
                    baseURL: settings.serverURL, token: settings.bearerToken) else { return }

                // Preview laden (unabhängig von has_preview im gecachten Job-Objekt)
                if previewImage == nil, !previewLoading {
                    previewLoading = true
                    if let data = try? await client.jobPreview(jobId: job.job_id),
                       let img = UIImage(data: data) {
                        previewImage = img
                    }
                    previewLoading = false
                }

                // Status pollen solange nicht terminal (max 15 × 20s = 5 min)
                var retries = 0
                while !PrintJob.isTerminal(displayStatus), retries < 15 {
                    try? await Task.sleep(for: .seconds(20))
                    guard !Task.isCancelled else { break }
                    if let r = try? await client.jobStatus(jobId: job.job_id),
                       r.status != displayStatus {
                        displayStatus = r.status
                        // Cache + Liste im Hintergrund aktualisieren
                        let updated = job.withUpdatedStatus(r.status)
                        if let idx = cache.jobs.firstIndex(where: { $0.job_id == job.job_id }) {
                            cache.jobs[idx] = updated
                        }
                    }
                    retries += 1
                }
            }
        }
    }

    @ViewBuilder
    private var previewSection: some View {
        if previewLoading {
            HStack {
                Spacer()
                ProgressView()
                Spacer()
            }
            .padding(.vertical, 24)
        } else if let img = previewImage {
            Button {
                previewFullscreen = true
            } label: {
                Image(uiImage: img)
                    .resizable()
                    .scaledToFit()
                    .frame(maxWidth: .infinity)
                    .cornerRadius(6)
                    .padding(.vertical, 4)
            }
            .buttonStyle(.plain)
            .fullScreenCover(isPresented: $previewFullscreen) {
                FullscreenImagePreview(image: img)
            }
        }
    }

    @ViewBuilder
    private func detailRow(_ label: String, value: String,
                           icon: String? = nil, iconColor: Color = .secondary) -> some View {
        HStack(alignment: .top, spacing: 8) {
            if let icon = icon {
                Image(systemName: icon)
                    .foregroundColor(iconColor)
                    .frame(width: 18)
                    .font(.subheadline)
            }
            Text(label)
                .foregroundColor(.secondary)
            Spacer()
            Text(value)
                .multilineTextAlignment(.trailing)
                .textSelection(.enabled)
        }
        .font(.subheadline)
    }

    private func formatBytes(_ bytes: Int) -> String {
        let b = Double(bytes)
        if b < 1_024 { return "\(bytes) B" }
        if b < 1_024 * 1_024 { return String(format: "%.1f KB", b / 1_024) }
        return String(format: "%.1f MB", b / (1_024 * 1_024))
    }
}

// MARK: - Fullscreen Preview

private struct FullscreenImagePreview: View {
    let image: UIImage
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            Image(uiImage: image)
                .resizable()
                .scaledToFit()
        }
        .overlay(alignment: .topTrailing) {
            Button { dismiss() } label: {
                Image(systemName: "xmark.circle.fill")
                    .font(.title)
                    .foregroundColor(.white)
                    .shadow(color: .black.opacity(0.6), radius: 3)
                    .padding()
            }
        }
    }
}

// MARK: - Data Models  (internal — AppCache liest sie auch)

struct PrintJob: Decodable, Identifiable, Equatable {
    let job_id: String
    let filename: String
    let status: String
    let queue: String
    let created_at: String
    let forwarded_at: String?
    let error_message: String?
    let source_identity: String?
    let delegated_from: String?
    let hostname: String?
    let data_size: Int?
    let has_preview: Bool?
    let delegate_recipients: [String]?
    let delegate_group_name: String?

    var id: String { job_id }

    static func == (lhs: PrintJob, rhs: PrintJob) -> Bool { lhs.job_id == rhs.job_id }

    init(job_id: String, filename: String, status: String, queue: String,
         created_at: String, forwarded_at: String? = nil, error_message: String? = nil,
         source_identity: String? = nil, delegated_from: String? = nil,
         hostname: String? = nil, data_size: Int? = nil, has_preview: Bool? = false,
         delegate_recipients: [String]? = nil, delegate_group_name: String? = nil) {
        self.job_id            = job_id
        self.filename          = filename
        self.status            = status
        self.queue             = queue
        self.created_at        = created_at
        self.forwarded_at      = forwarded_at
        self.error_message     = error_message
        self.source_identity   = source_identity
        self.delegated_from    = delegated_from
        self.hostname          = hostname
        self.data_size         = data_size
        self.has_preview       = has_preview
        self.delegate_recipients  = delegate_recipients
        self.delegate_group_name  = delegate_group_name
    }

    var badgeStyle: (Color, String) { PrintJob.badgeStyleFor(status) }

    static func badgeStyleFor(_ s: String) -> (Color, String) {
        switch s.lowercased() {
        case "queued", "waiting_for_upload":
            return (.gray,    String(localized: "In Warteschlange"))
        case "sent", "forwarded":
            return (MSP.cyan, String(localized: "An Printix gesendet"))
        case "converting":
            return (.orange,  String(localized: "Konvertierung…"))
        case "ready":
            return (MSP.cyan, String(localized: "Bereit am Drucker"))
        case "printing", "received", "pending", "processing":
            return (.orange,  String(localized: "Wird gedruckt…"))
        case "ok", "success", "completed", "printed":
            return (.green,   String(localized: "Erfolgreich gedruckt ✓"))
        case "expired", "deleted":
            return (.gray,    String(localized: "Abgelaufen"))
        case "error", "failed":
            return (.red,     String(localized: "Fehler beim Drucken"))
        default:
            return (.gray,    s)
        }
    }

    static func isTerminal(_ s: String) -> Bool {
        ["printed", "ok", "success", "completed", "error", "failed", "expired", "deleted"]
            .contains(s.lowercased())
    }

    func withUpdatedStatus(_ newStatus: String) -> PrintJob {
        PrintJob(job_id: job_id, filename: filename, status: newStatus, queue: queue,
                 created_at: created_at, forwarded_at: forwarded_at,
                 error_message: error_message, source_identity: source_identity,
                 delegated_from: delegated_from, hostname: hostname, data_size: data_size,
                 has_preview: has_preview, delegate_recipients: delegate_recipients,
                 delegate_group_name: delegate_group_name)
    }

    static func formatDate(_ raw: String, style: DateFormatter.Style) -> String {
        guard !raw.isEmpty else { return "-" }
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let date = iso.date(from: raw) ?? ISO8601DateFormatter().date(from: raw)
        guard let d = date else { return raw }
        let df = DateFormatter()
        df.dateStyle = style
        df.timeStyle = .short
        return df.string(from: d)
    }
}

struct JobsResponse: Decodable {
    let jobs: [PrintJob]
    let count: Int
}
