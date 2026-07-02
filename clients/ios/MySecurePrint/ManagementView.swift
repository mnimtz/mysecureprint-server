import SwiftUI
import PrintixSendCore

/// Printix Management — Read-only Live-Uebersicht fuers Tenant (v1, iOS-Only).
///
/// Architektur-Entscheidung: reine On-Demand-Abfrage, kein Cache im Client.
/// Jeder "Aktualisieren"-Tap + jedes Oeffnen des Tabs feuert die drei
/// Endpoints (/stats, /printers, /users, /workstations) parallel via
/// async-let. Damit sehen Admins den aktuellen Zustand — kein stale State.
/// Push/Background-Poll ist Phase 2.
///
/// Sichtbarkeit: nur wenn `settings.hasManagementAccess` true ist. Die
/// Bedingung entscheidet ContentView → MainTabs, nicht diese View selbst.
struct ManagementView: View {
    @EnvironmentObject private var settings: SettingsStore

    @State private var stats: MgmtStatsResponse?
    @State private var printers: [MgmtPrinter] = []
    @State private var users: [MgmtUser] = []
    @State private var workstations: [MgmtWorkstation] = []

    @State private var isLoading = false
    @State private var lastUpdated: Date?
    @State private var errorMessage: String?

    // Aufklapp-Status der Detaillisten. Alle drei Listen starten
    // eingeklappt — nur die Uebersicht + Zaehler sind sofort sichtbar,
    // damit die Seite nicht ueberladen wirkt.
    @State private var expandPrinters = false
    @State private var expandUsers = false
    @State private var expandWorkstations = false

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
                // aktiv aus — Belt-and-suspenders.
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
            .refreshable { await reload() }
            .navigationTitle(String(localized: "mgmt_nav_title"))
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task { await reload() }
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
                if stats == nil { await reload() }
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
                        VStack(alignment: .leading, spacing: 2) {
                            Text(u.name ?? u.email ?? u.id).font(.body)
                            if let e = u.email, !e.isEmpty, e != u.name {
                                Text(e).font(.caption).foregroundStyle(.secondary)
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

    /// Einheitliches Header-Layout fuer alle drei Disclosure-Listen:
    /// Icon + Titel links, Anzahl rechts. Tippen oeffnet/schliesst die
    /// Liste. Das Chevron zeichnet SwiftUI automatisch dazu.
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

    private func reload() async {
        guard let base = settings.serverBaseURL,
              let client = ApiClientFactory.make(baseURL: base.absoluteString,
                                                 token: settings.bearerToken) else {
            errorMessage = String(localized: "Kein Server konfiguriert")
            return
        }

        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        // Jeden Endpoint einzeln in try?-Tasks — ein Decode-Fehler in
        // /users soll nicht verhindern, dass /printers und /workstations
        // trotzdem angezeigt werden. Fehlgeschlagene Endpoints sammeln
        // wir in einer Liste, damit wir gezielt sagen koennen WAS kaputt
        // ist.
        async let statsResult         = runNamed("stats")        { try await client.managementStats()         }
        async let printersResult      = runNamed("printers")     { try await client.managementPrinters()      }
        async let usersResult         = runNamed("users")        { try await client.managementUsers()         }
        async let workstationsResult  = runNamed("workstations") { try await client.managementWorkstations()  }

        let (sR, pR, uR, wR) = await (statsResult, printersResult,
                                      usersResult, workstationsResult)

        stats = sR.value
        printers = pR.value?.printers ?? []
        users = uR.value?.users ?? []
        workstations = wR.value?.workstations ?? []
        lastUpdated = Date()

        // Heterogene Generic-Typen → wir koennen die NamedResults nicht
        // in ein Array packen, also Fehler einzeln einsammeln.
        var failures: [String] = []
        if let f = sR.failure { failures.append(f) }
        if let f = pR.failure { failures.append(f) }
        if let f = uR.failure { failures.append(f) }
        if let f = wR.failure { failures.append(f) }
        if !failures.isEmpty {
            // no_tenant → freundlicher Hinweis statt rohem HTTP-String
            if failures.first?.contains("no_tenant") == true
                || failures.first?.contains("no tenant") == true {
                errorMessage = String(localized: "Printix-API nicht konfiguriert. Bitte im Admin-Portal unter Einstellungen → Printix die API-Zugangsdaten eintragen.")
            } else {
                errorMessage = failures.joined(separator: "\n")
            }
        }
    }

    /// Wrapper fuer eine benannte, fehlertolerante Async-Task. Liefert
    /// entweder einen value oder eine description des Fehlers — beides
    /// nicht beide.
    private func runNamed<T>(_ name: String,
                             _ op: @escaping () async throws -> T) async -> NamedResult<T> {
        do {
            let v = try await op()
            return NamedResult(value: v, failure: nil)
        } catch {
            // Debug-Detail statt localizedDescription — bei Decode-
            // Fehlern zeigt Foundation sonst nur "The data couldn't
            // be read…" ohne den eigentlichen Typ-Mismatch.
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
