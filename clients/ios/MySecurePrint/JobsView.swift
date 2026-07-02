import SwiftUI
import PrintixSendCore

/// Jobs-Tab: zeigt die letzten Druck-Jobs des angemeldeten Users.
/// Daten kommen aus /desktop/me/jobs (Server-Endpoint seit v0.6.0).
///
/// Pull-to-refresh laedt neu. Beim Anzeigen wird automatisch einmal
/// geholt. Kein Push noetig — User wechselt nach Send einfach in den
/// Tab und sieht den frischen Eintrag.
struct JobsView: View {

    @EnvironmentObject private var settings: SettingsStore

    @State private var jobs: [PrintJob] = []
    @State private var loading: Bool = false
    @State private var error: String = ""

    var body: some View {
        NavigationStack {
            List {
                if loading && jobs.isEmpty {
                    HStack { ProgressView(); Text(String(localized: "Lade Jobs …")) }
                } else if jobs.isEmpty && !loading {
                    Section {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(String(localized: "Noch keine Druck-Jobs"))
                                .font(.headline)
                            Text(String(localized: "Hier landen deine Aufträge nach dem Senden — inkl. Status (bereit zur Abholung, fehlgeschlagen)."))
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                        .padding(.vertical, 8)
                    }
                } else {
                    ForEach(jobs) { job in
                        JobRow(job: job)
                    }
                }

                if !error.isEmpty {
                    Section(String(localized: "Fehler")) {
                        Text(error).foregroundColor(.red).textSelection(.enabled)
                    }
                }
            }
            .brandNavStyle(title: String(localized: "Meine Jobs"))
            .refreshable { await reload() }
            .task { await reload() }
        }
    }

    @MainActor
    private func reload() async {
        error = ""
        guard !settings.serverURL.isEmpty,
              !settings.bearerToken.isEmpty else {
            error = String(localized: "Nicht angemeldet.")
            return
        }
        loading = true
        defer { loading = false }
        do {
            jobs = try await fetchJobs()
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func fetchJobs() async throws -> [PrintJob] {
        // H-1: ueber den zentralen ApiClient — kein force-unwrap, kein
        // manuelles URL-Konkat. Fehler kommen als ApiError mit
        // lesbarer Message.
        guard let client = ApiClientFactory.make(baseURL: settings.serverURL,
                                                 token: settings.bearerToken) else {
            throw ApiError.invalidUrl
        }
        let data = try await client.myJobs(limit: 30)
        let decoded = try JSONDecoder().decode(JobsResponse.self, from: data)
        return decoded.jobs
    }
}

private struct JobRow: View {
    let job: PrintJob

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(job.filename.isEmpty ? String(localized: "(ohne Namen)") : job.filename)
                    .font(.body)
                    .lineLimit(1)
                Spacer()
                statusBadge
            }
            HStack(spacing: 8) {
                if !job.queue.isEmpty {
                    Label(job.queue, systemImage: "printer.fill")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                Text(displayDate)
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            if let err = job.error_message, !err.isEmpty {
                Text(err)
                    .font(.caption2)
                    .foregroundColor(.red)
                    .lineLimit(2)
            }
        }
        .padding(.vertical, 4)
    }

    private var displayDate: String {
        let raw = job.forwarded_at ?? job.created_at
        guard !raw.isEmpty else { return "" }
        // Server sendet ISO-8601. Wir zeigen nur Datum + Uhrzeit kompakt.
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let date = iso.date(from: raw) ?? ISO8601DateFormatter().date(from: raw)
        guard let d = date else { return raw }
        let df = DateFormatter()
        df.dateStyle = .short
        df.timeStyle = .short
        return df.string(from: d)
    }

    @ViewBuilder
    private var statusBadge: some View {
        let (color, label) = badgeStyle
        Text(label)
            .font(.caption)
            .fontWeight(.semibold)
            .padding(.horizontal, 8)
            .padding(.vertical, 2)
            .background(color.opacity(0.15))
            .foregroundColor(color)
            .clipShape(Capsule())
    }

    private var badgeStyle: (Color, String) {
        switch job.status.lowercased() {
        case "forwarded", "ok", "sent", "success":
            return (.green, String(localized: "Bereit zur Abholung"))
        case "received", "pending":
            return (.orange, String(localized: "Wird verarbeitet…"))
        case "error", "failed":
            return (.red, String(localized: "Fehlgeschlagen"))
        default:
            return (.gray, job.status)
        }
    }
}

// MARK: - Data models

struct PrintJob: Decodable, Identifiable {
    let job_id: String
    let filename: String
    let status: String
    let queue: String
    let created_at: String
    let forwarded_at: String?
    let error_message: String?
    let source_identity: String?

    var id: String { job_id }
}

private struct JobsResponse: Decodable {
    let jobs: [PrintJob]
    let count: Int
}
