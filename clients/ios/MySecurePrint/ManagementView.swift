import SwiftUI
import Combine
import PrintixSendCore

/// Printix Management — Read-only Live-Uebersicht fuers Tenant.
///
/// Preload-Strategie: AppCache befüllt Management-Daten beim App-Start im
/// Hintergrund. ManagementView zeigt den Cache sofort an und triggert
/// anschliessend einen stillen Hintergrund-Refresh. Alle 5 Minuten wird
/// automatisch nachgeladen (nur wenn der Tab sichtbar ist).
///
/// Sichtbarkeit: nur wenn `settings.hasManagementAccess` true ist. Die
/// Bedingung entscheidet ContentView → MainTabs, nicht diese View selbst.
struct ManagementView: View {
    @EnvironmentObject private var settings: SettingsStore
    @EnvironmentObject private var cache: AppCache

    @State private var stats: MgmtStatsResponse?
    @State private var printers: [MgmtPrinter] = []
    @State private var users: [MgmtUser] = []
    @State private var workstations: [MgmtWorkstation] = []

    @State private var isLoading = false
    @State private var lastUpdated: Date?
    @State private var errorMessage: String?

    // Aufklapp-Status der Detaillisten.
    @State private var expandPrinters = false
    @State private var expandUsers = false
    @State private var expandWorkstations = false

    // 5-Minuten-Timer für stillen Hintergrund-Refresh (läuft nur wenn Tab sichtbar).
    private let refreshTimer = Timer.publish(every: 300, on: .main, in: .common).autoconnect()

    var body: some View {
        NavigationStack {
            List {
                statsSection
                printersSection
                // Defense-in-depth: Benutzer + Arbeitsplaetze nur fuer
                // Admin/User. Employees sehen den ManagementView ohnehin
                // nicht (gated via hasManagementAccess in ContentView),
                // aber falls die Rolle nach App-Start zu "employee"
                // wechselt blenden wir die sensiblen Listen hier nochmal
                // aktiv aus.
                if settings.userRoleType.lowercased() != "employee" {
                    usersSection
                    workstationsSection
                }
                if let err = errorMessage {
                    Section {
                        Label(err, systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(.red)
                            .font(.footnote)
                    }
                }
                if let ts = lastUpdated {
                    Section {
                        Text(String(format: String(localized: "mgmt_last_updated"), formattedTime(ts)))
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            // NavigationLink-Destinationen für alle drei Detailansichten
            .navigationDestination(for: MgmtPrinter.self) { p in
                PrinterDetailView(printer: p)
            }
            .navigationDestination(for: MgmtUser.self) { u in
                UserDetailView(user: u)
            }
            .navigationDestination(for: MgmtWorkstation.self) { w in
                WorkstationDetailView(workstation: w)
            }
            .refreshable { await reload(updateCache: true) }
            .brandNavStyle(title: String(localized: "mgmt_nav_title"))
            .tint(MSP.cyan)
            .listStyle(.insetGrouped)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task { await reload(updateCache: true) }
                    } label: {
                        if isLoading {
                            ProgressView()
                        } else {
                            Label(String(localized: "mgmt_refresh"), systemImage: "arrow.clockwise")
                        }
                    }
                    .disabled(isLoading)
                }
            }
            .task {
                // Cache sofort anzeigen (kein Flackern / Ladescreen),
                // dann im Hintergrund frisch laden und Cache aktualisieren.
                if cache.mgmtStats != nil {
                    applyCache()
                    await reload(updateCache: true)
                } else {
                    await reload(updateCache: true)
                }
            }
            // Stiller Hintergrund-Refresh alle 5 Minuten
            .onReceive(refreshTimer) { _ in
                Task { await reload(updateCache: true) }
            }
        }
    }

    // MARK: - Sections

    @ViewBuilder
    private var statsSection: some View {
        Section(String(localized: "mgmt_section_overview")) {
            statRow(icon: "printer.fill",
                    label: "mgmt_stat_printers",
                    bucket: stats?.printers,
                    onlineLabel: String(localized: "online"))
            if settings.userRoleType.lowercased() != "employee" {
                statRow(icon: "person.2.fill",
                        label: "mgmt_stat_users",
                        bucket: stats?.users,
                        onlineLabel: nil)
                statRow(icon: "desktopcomputer",
                        label: "mgmt_stat_workstations",
                        bucket: stats?.workstations,
                        onlineLabel: String(localized: "online"))
            }
            if let name = stats?.tenant?.name, !name.isEmpty {
                HStack {
                    Image(systemName: "building.2")
                        .foregroundStyle(.secondary)
                        .frame(width: 26)
                    Text("mgmt_stat_tenant").foregroundStyle(.secondary)
                    Spacer()
                    Text(name).font(.footnote.monospaced())
                }
            }
        }
    }

    @ViewBuilder
    private func statRow(icon: String, label: LocalizedStringKey,
                         bucket: MgmtStatsBucket?, onlineLabel: String?) -> some View {
        HStack {
            Image(systemName: icon)
                .foregroundStyle(.tint)
                .frame(width: 26)
            Text(label)
            Spacer()
            if bucket?.available == false {
                Text(String(localized: "mgmt_not_available")).foregroundStyle(.secondary).font(.footnote)
            } else if let b = bucket {
                if let online = b.online, let total = b.total, let lbl = onlineLabel {
                    Text("\(online)/\(total) \(lbl)")
                        .font(.callout.monospacedDigit())
                } else {
                    Text("\(b.total ?? 0)").font(.callout.monospacedDigit())
                }
            } else if isLoading {
                ProgressView().controlSize(.small)
            } else {
                Text("—").foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    private var printersSection: some View {
        if !printers.isEmpty {
            Section {
                DisclosureGroup(isExpanded: $expandPrinters) {
                    ForEach(printers) { p in
                        NavigationLink(value: p) {
                            HStack(alignment: .firstTextBaseline) {
                                Circle()
                                    .fill(p.isOnline == true ? Color.green : Color.gray)
                                    .frame(width: 8, height: 8)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(p.name).font(.body)
                                    if let loc = p.location, !loc.isEmpty {
                                        Text(loc).font(.caption).foregroundStyle(.secondary)
                                    } else if let m = p.model, !m.isEmpty {
                                        Text(m).font(.caption).foregroundStyle(.secondary)
                                    }
                                }
                                Spacer()
                                if let s = p.status, !s.isEmpty {
                                    Text(s).font(.caption2).foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                } label: {
                    disclosureLabel(icon: "printer.fill",
                                    title: "Drucker",
                                    count: printers.count)
                }
            }
        }
    }

    @ViewBuilder
    private var usersSection: some View {
        if !users.isEmpty {
            Section {
                DisclosureGroup(isExpanded: $expandUsers) {
                    ForEach(users.prefix(50)) { u in
                        NavigationLink(value: u) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(u.name ?? u.email ?? u.id).font(.body)
                                if let e = u.email, !e.isEmpty, e != u.name {
                                    Text(e).font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                    if users.count > 50 {
                        Text("… \(users.count - 50) weitere")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                } label: {
                    disclosureLabel(icon: "person.2.fill",
                                    title: "Benutzer",
                                    count: users.count)
                }
            }
        }
    }

    @ViewBuilder
    private var workstationsSection: some View {
        if !workstations.isEmpty {
            Section {
                DisclosureGroup(isExpanded: $expandWorkstations) {
                    ForEach(workstations.prefix(50)) { w in
                        NavigationLink(value: w) {
                            HStack(alignment: .firstTextBaseline) {
                                Circle()
                                    .fill(w.isOnline == true ? Color.green : Color.gray)
                                    .frame(width: 8, height: 8)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(w.hostname).font(.body)
                                    if let e = w.userEmail, !e.isEmpty {
                                        Text(e).font(.caption).foregroundStyle(.secondary)
                                    }
                                }
                                Spacer()
                            }
                        }
                    }
                    if workstations.count > 50 {
                        Text("… \(workstations.count - 50) weitere")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                } label: {
                    disclosureLabel(icon: "desktopcomputer",
                                    title: "Arbeitsplätze",
                                    count: workstations.count)
                }
            }
        }
    }

    @ViewBuilder
    private func disclosureLabel(icon: String, title: LocalizedStringKey, count: Int) -> some View {
        HStack {
            Image(systemName: icon)
                .foregroundStyle(.tint)
                .frame(width: 26)
            Text(title).font(.body)
            Spacer()
            Text("\(count)")
                .font(.callout.monospacedDigit())
                .foregroundStyle(.secondary)
        }
        .contentShape(Rectangle())
    }

    // MARK: - Reload

    private func applyCache() {
        stats        = cache.mgmtStats
        printers     = cache.mgmtPrinters
        users        = cache.mgmtUsers
        workstations = cache.mgmtWorkstations
        lastUpdated  = cache.mgmtLastSyncedAt
    }

    private func reload(updateCache: Bool = false) async {
        guard let base = settings.serverBaseURL,
              let client = ApiClientFactory.make(baseURL: base.absoluteString,
                                                 token: settings.bearerToken) else {
            errorMessage = String(localized: "Kein Server konfiguriert")
            return
        }

        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        async let statsResult         = runNamed("stats")        { try await client.managementStats()         }
        async let printersResult      = runNamed("printers")     { try await client.managementPrinters()      }
        async let usersResult         = runNamed("users")        { try await client.managementUsers()         }
        async let workstationsResult  = runNamed("workstations") { try await client.managementWorkstations()  }

        let (sR, pR, uR, wR) = await (statsResult, printersResult,
                                      usersResult, workstationsResult)

        stats        = sR.value
        printers     = pR.value?.printers ?? []
        users        = uR.value?.users ?? []
        workstations = wR.value?.workstations ?? []
        lastUpdated  = Date()

        if updateCache {
            cache.mgmtStats        = stats
            cache.mgmtPrinters     = printers
            cache.mgmtUsers        = users
            cache.mgmtWorkstations = workstations
            cache.mgmtLastSyncedAt = lastUpdated
        }

        var failures: [String] = []
        if let f = sR.failure { failures.append(f) }
        if let f = pR.failure { failures.append(f) }
        if let f = uR.failure { failures.append(f) }
        if let f = wR.failure { failures.append(f) }
        if !failures.isEmpty {
            if failures.first?.contains("no_tenant") == true
                || failures.first?.contains("no tenant") == true {
                errorMessage = String(localized: "Printix-API nicht konfiguriert. Bitte im Admin-Portal unter Einstellungen → Printix die API-Zugangsdaten eintragen.")
            } else {
                errorMessage = failures.joined(separator: "\n")
            }
        }
    }

    private func runNamed<T>(_ name: String,
                             _ op: @escaping () async throws -> T) async -> NamedResult<T> {
        do {
            let v = try await op()
            return NamedResult(value: v, failure: nil)
        } catch {
            let detail = "\(error)"
            return NamedResult(value: nil, failure: "\(name): \(detail.prefix(280))")
        }
    }

    private struct NamedResult<T> {
        let value: T?
        let failure: String?
    }

    private func formattedTime(_ d: Date) -> String {
        let f = DateFormatter()
        f.timeStyle = .medium
        f.dateStyle = .none
        return f.string(from: d)
    }
}
