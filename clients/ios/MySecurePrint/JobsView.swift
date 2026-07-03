import SwiftUI
import PrintixSendCore

// MARK: - Jobs Tab

struct JobsView: View {

    @EnvironmentObject private var settings: SettingsStore

    @State private var jobs: [PrintJob] = []
    @State private var loading = false
    @State private var loadingMore = false
    @State private var error = ""
    @State private var searchText = ""
    @State private var hasMore = false
    @State private var selectedJob: PrintJob? = nil
    @State private var showClearConfirm = false

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
            .refreshable { await reload() }
            .task { await reload() }
            .sheet(item: $selectedJob) { job in
                JobDetailView(job: job)
            }
            .toolbar {
                ToolbarItem(placement: .bottomBar) {
                    if !jobs.isEmpty {
                        Button(role: .destructive) {
                            showClearConfirm = true
                        } label: {
                            Label(String(localized: "Verlauf löschen"), systemImage: "trash")
                                .font(.caption)
                        }
                        .foregroundStyle(.secondary)
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
        defer { loading = false }
        do {
            let result = try await fetchJobs(offset: 0)
            jobs = result.jobs
            hasMore = result.jobs.count >= pageSize
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
            // Delegate flag
            if let delegate = job.delegated_from, !delegate.isEmpty {
                delegateTag(name: delegate)
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
    @State private var previewImage: UIImage? = nil
    @State private var previewLoading = false
    @State private var previewFullscreen = false

    var body: some View {
        NavigationStack {
            List {
                // Vorschau-Bild (falls vorhanden)
                if job.has_preview == true {
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
                        detailRow(String(localized: "Delegiert an"), value: delegate,
                                  icon: "person.2.fill", iconColor: MSP.cyan)
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
        }
    }

    @ViewBuilder
    private var previewSection: some View {
        Group {
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
                    ZStack(alignment: .topTrailing) {
                        Color.black.ignoresSafeArea()
                        Image(uiImage: img)
                            .resizable()
                            .scaledToFit()
                            .ignoresSafeArea()
                        Button {
                            previewFullscreen = false
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .font(.title)
                                .foregroundColor(.white)
                                .padding()
                        }
                    }
                }
            }
        }
        .task {
            guard previewImage == nil, !previewLoading,
                  let client = ApiClientFactory.make(
                    baseURL: settings.serverURL, token: settings.bearerToken) else { return }
            previewLoading = true
            defer { previewLoading = false }
            if let data = try? await client.jobPreview(jobId: job.job_id),
               let img = UIImage(data: data) {
                previewImage = img
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

// MARK: - Data Models

struct PrintJob: Decodable, Identifiable {
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

    var id: String { job_id }

    var badgeStyle: (Color, String) {
        switch status.lowercased() {
        case "queued":
            return (.gray,   String(localized: "In Warteschlange"))
        case "sent", "forwarded":
            return (MSP.cyan, String(localized: "An Printix gesendet"))
        case "received", "pending", "processing":
            return (.orange,  String(localized: "Wird gedruckt…"))
        case "ok", "success", "completed", "printed":
            return (.green,   String(localized: "Erfolgreich gedruckt ✓"))
        case "expired", "deleted":
            return (.gray,    String(localized: "Abgelaufen"))
        case "error", "failed":
            return (.red,     String(localized: "Fehler beim Drucken"))
        default:
            return (.gray,   status)
        }
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

private struct JobsResponse: Decodable {
    let jobs: [PrintJob]
    let count: Int
}
