import SwiftUI
import PrintixSendCore

/// Zeigt die Liste der verfügbaren Druck-Ziele (Queues/Printer aus
/// Printix). Auswahl speichert die Target-Id im SettingsStore, damit
/// UploadView + Share-Extension das gleiche Default-Ziel verwenden.
struct TargetsView: View {

    @EnvironmentObject private var settings: SettingsStore

    @State private var targets: [Target] = []
    @State private var loading: Bool = false
    @State private var error: String = ""

    // Delegation-User-Picker (Task D, v0.6.x)
    //
    // Wenn `delegateEnabled` true ist, kann der User eine Liste von
    // weiteren Printix-Benutzern anvisieren. Wir laden die Liste lazy
    // ueber /desktop/management/users (admin/user-only — Employees
    // bekommen 403, der Picker zeigt dann einen Hinweis). Die Auswahl
    // landet als id "print:user:<printix_user_id>" in
    // settings.selectedTargetIds, sodass UploadView automatisch je
    // einen Job pro Ziel raussendet. Server unterstuetzt den
    // "print:user:"-Prefix in desktop_routes.py:_process_desktop_send_bg.
    @State private var allMgmtUsers: [MgmtUser] = []
    @State private var mgmtUsersLoaded: Bool = false
    @State private var mgmtUsersLoading: Bool = false
    @State private var mgmtUsersError: String = ""
    @State private var userQuery: String = ""

    // v0.6.7: Queue-Picker — wenn der Server-Admin "User darf Queue waehlen"
    // aktiviert hat (Feld user_can_choose in /desktop/targets), bietet
    // dieser Tab zusaetzlich einen Button "Andere Queue waehlen" der
    // /desktop/queues laedt und einen Picker zeigt. Selection landet als
    // "print:queue:<queue_id>" in settings.selectedTargetIds.
    @State private var userCanChoose: Bool = false
    @State private var showQueuePicker: Bool = false
    @State private var allQueues: [QueueItem] = []
    @State private var queuesLoading: Bool = false
    @State private var queuesError: String = ""
    @State private var queueQuery: String = ""

    var body: some View {
        NavigationStack {
            List {
                if let account = accountSummary {
                    Section("Account") {
                        Text(account).font(.footnote).foregroundColor(.secondary)
                    }
                }

                if settings.selectionExpiresAt != nil {
                    Section {
                        TimelineView(.periodic(from: .now, by: 1)) { ctx in
                            autoResetHint(now: ctx.date)
                        }
                    }
                }

                Section(String(localized: "targets_section_title")) {
                    if loading && targets.isEmpty {
                        HStack { ProgressView(); Text(String(localized: "targets_loading")) }
                    } else if targets.isEmpty {
                        Text(String(localized: "targets_empty"))
                            .foregroundColor(.secondary)
                    } else {
                        ForEach(targets) { t in
                            Button {
                                toggle(t)
                            } label: {
                                HStack {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(localizedTargetLabel(t)).foregroundColor(.primary)
                                        if let s = t.subtitle, !s.isEmpty {
                                            Text(s).font(.caption).foregroundColor(.secondary)
                                        }
                                    }
                                    Spacer()
                                    // Multi-Select: Haken wenn das Ziel in der
                                    // ausgewaehlten Liste steht. Tap toggelt
                                    // Mitgliedschaft — so kann man mehrere
                                    // Ziele gleichzeitig anvisieren.
                                    if settings.selectedTargetIds.contains(t.id) {
                                        Image(systemName: "checkmark.circle.fill")
                                            .foregroundColor(.accentColor)
                                    } else {
                                        Image(systemName: "circle")
                                            .foregroundColor(.secondary)
                                    }
                                }
                            }
                        }
                    }
                }

                if userCanChoose {
                    queuePickerSection
                }

                if settings.delegateEnabled {
                    delegationPickerSection
                }

                if !error.isEmpty {
                    Section(String(localized: "error_section_title")) {
                        Text(error).foregroundColor(.red).textSelection(.enabled)
                    }
                }

            }
            .navigationTitle(String(localized: "targets_nav_title"))
            .refreshable {
                await reload()
                if settings.delegateEnabled { await reloadMgmtUsers() }
            }
            .task {
                await reload()
                if settings.delegateEnabled { await reloadMgmtUsers() }
            }
            .onChange(of: settings.delegateEnabled) { _, enabled in
                if enabled && !mgmtUsersLoaded {
                    Task { await reloadMgmtUsers() }
                }
            }
        }
    }

    /// Aktuell ausgewaehlte „print:user:…"-Ziele (User-Delegation, Task D).
    private var selectedUserDelegationIds: [String] {
        settings.selectedTargetIds.filter { $0.hasPrefix("print:user:") }
    }

    /// Liste der Mgmt-User minus dem eingeloggten User selbst (man kann
    /// sich nicht selber als Delegation-Ziel waehlen — dafuer gibt's
    /// "print:self") minus bereits ausgewaehlte.
    private var filteredMgmtUsers: [MgmtUser] {
        let q = userQuery.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let me = settings.userEmail.lowercased()
        let selectedPxIds: Set<String> = Set(selectedUserDelegationIds.map {
            String($0.dropFirst("print:user:".count))
        })
        return allMgmtUsers.filter { u in
            // eigenen User raus
            if let e = u.email?.lowercased(), e == me { return false }
            // schon ausgewaehlte raus
            if selectedPxIds.contains(u.id) { return false }
            if q.isEmpty { return true }
            let name = (u.name ?? "").lowercased()
            let mail = (u.email ?? "").lowercased()
            return name.contains(q) || mail.contains(q)
        }
    }

    /// v0.6.7: Aktuell ausgewaehlte "print:queue:…"-Ziele (Queue-Picker).
    private var selectedQueueTargetIds: [String] {
        settings.selectedTargetIds.filter { $0.hasPrefix("print:queue:") }
    }

    private var filteredQueues: [QueueItem] {
        let q = queueQuery.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let selected = Set(selectedQueueTargetIds)
        return allQueues.filter { item in
            if selected.contains(item.id) { return false }
            if q.isEmpty { return true }
            let name = item.queueName.lowercased()
            let printer = (item.printerName ?? "").lowercased()
            let loc = (item.location ?? "").lowercased()
            return name.contains(q) || printer.contains(q) || loc.contains(q)
        }
    }

    @ViewBuilder
    private var queuePickerSection: some View {
        Section {
            VStack(alignment: .leading, spacing: 6) {
                Text(String(localized: "queue_picker_title"))
                    .font(.headline)
                Text(String(localized: "queue_picker_sub"))
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            // Bereits ausgewaehlte Queue-Picks mit Entfernen-X.
            ForEach(selectedQueueTargetIds, id: \.self) { id in
                HStack {
                    Image(systemName: "printer.dotmatrix")
                        .foregroundColor(.accentColor)
                    Text(settings.targetLabels[id] ?? id)
                        .font(.body)
                    Spacer()
                    Button {
                        removeQueueSelection(id)
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundColor(.secondary)
                    }
                    .buttonStyle(.plain)
                }
            }

            if !showQueuePicker {
                Button {
                    showQueuePicker = true
                    if allQueues.isEmpty { Task { await reloadQueues() } }
                } label: {
                    Label("Queue suchen", systemImage: "magnifyingglass")
                }
            } else {
                HStack {
                    Image(systemName: "magnifyingglass").foregroundColor(.secondary)
                    TextField("Queue suchen (Name, Drucker, Standort)", text: $queueQuery)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    if !queueQuery.isEmpty {
                        Button { queueQuery = "" } label: {
                            Image(systemName: "xmark.circle.fill").foregroundColor(.secondary)
                        }
                        .buttonStyle(.plain)
                    }
                }

                if queuesLoading && allQueues.isEmpty {
                    HStack { ProgressView(); Text(String(localized: "queues_loading")) }
                } else if !queuesError.isEmpty {
                    Text(queuesError)
                        .font(.caption)
                        .foregroundColor(.orange)
                } else if allQueues.isEmpty {
                    Text(String(localized: "queues_empty"))
                        .font(.caption)
                        .foregroundColor(.secondary)
                } else {
                    let visible = filteredQueues.prefix(20)
                    if visible.isEmpty && !queueQuery.isEmpty {
                        Text("Keine Treffer.")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    ForEach(Array(visible), id: \.id) { q in
                        Button { addQueueSelection(q) } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    HStack(spacing: 4) {
                                        Text(q.queueName).foregroundColor(.primary)
                                        if q.isAnywhere == true {
                                            Text("Anywhere")
                                                .font(.caption2)
                                                .padding(.horizontal, 6)
                                                .padding(.vertical, 1)
                                                .background(Color.accentColor.opacity(0.15))
                                                .foregroundColor(.accentColor)
                                                .clipShape(Capsule())
                                        }
                                    }
                                    let sub = [q.printerName, q.location]
                                        .compactMap { $0?.trimmingCharacters(in: .whitespaces) }
                                        .filter { !$0.isEmpty }
                                        .joined(separator: " · ")
                                    if !sub.isEmpty {
                                        Text(sub).font(.caption).foregroundColor(.secondary)
                                    }
                                }
                                Spacer()
                                Image(systemName: "plus.circle")
                                    .foregroundColor(.accentColor)
                            }
                        }
                    }
                }
            }
        }
    }

    private func addQueueSelection(_ q: QueueItem) {
        if settings.selectedTargetIds.contains(q.id) { return }
        let label: String = {
            if let p = q.printerName, !p.isEmpty, p != q.queueName {
                return "\(q.queueName) (\(p))"
            }
            return q.queueName
        }()
        settings.targetLabels[q.id] = label
        settings.selectedTargetIds.append(q.id)
        settings.applyAutoResetPolicy()
        queueQuery = ""
    }

    private func removeQueueSelection(_ id: String) {
        settings.selectedTargetIds.removeAll { $0 == id }
        settings.targetLabels.removeValue(forKey: id)
        settings.applyAutoResetPolicy()
    }

    @MainActor
    private func reloadQueues() async {
        guard let client = ApiClientFactory.make(baseURL: settings.serverURL,
                                                 token: settings.bearerToken) else {
            return
        }
        queuesLoading = true
        queuesError = ""
        defer { queuesLoading = false }
        do {
            let resp = try await client.listQueues()
            allQueues = resp.queues
        } catch {
            queuesError = String(localized: "Queues konnten nicht geladen werden.")
        }
    }

    @ViewBuilder
    private var delegationPickerSection: some View {
        Section {
            VStack(alignment: .leading, spacing: 6) {
                Text(String(localized: "delegation_picker_title"))
                    .font(.headline)
                Text(String(localized: "delegation_picker_sub"))
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            // Bereits ausgewaehlte Delegation-Ziele mit Entfernen-X.
            ForEach(selectedUserDelegationIds, id: \.self) { id in
                HStack {
                    Image(systemName: "person.crop.circle.badge.checkmark")
                        .foregroundColor(.accentColor)
                    Text(settings.targetLabels[id] ?? id)
                        .font(.body)
                    Spacer()
                    Button {
                        removeUserDelegation(id)
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundColor(.secondary)
                    }
                    .buttonStyle(.plain)
                }
            }

            // Suchfeld
            HStack {
                Image(systemName: "magnifyingglass").foregroundColor(.secondary)
                TextField("Benutzer suchen (Name oder E-Mail)", text: $userQuery)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                if !userQuery.isEmpty {
                    Button { userQuery = "" } label: {
                        Image(systemName: "xmark.circle.fill").foregroundColor(.secondary)
                    }
                    .buttonStyle(.plain)
                }
            }

            if mgmtUsersLoading && !mgmtUsersLoaded {
                HStack { ProgressView(); Text(String(localized: "users_loading")) }
            } else if !mgmtUsersError.isEmpty {
                Text(mgmtUsersError)
                    .font(.caption)
                    .foregroundColor(.orange)
            } else if mgmtUsersLoaded && allMgmtUsers.isEmpty {
                Text(String(localized: "delegation_users_empty"))
                    .font(.caption)
                    .foregroundColor(.secondary)
            } else {
                let visible = filteredMgmtUsers.prefix(20)
                if visible.isEmpty && !userQuery.isEmpty {
                    Text("Keine Treffer.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                ForEach(Array(visible), id: \.id) { u in
                    Button { addUserDelegation(u) } label: {
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(u.name ?? u.email ?? u.id).foregroundColor(.primary)
                                if let e = u.email, !e.isEmpty, e != u.name {
                                    Text(e).font(.caption).foregroundColor(.secondary)
                                }
                            }
                            Spacer()
                            Image(systemName: "plus.circle")
                                .foregroundColor(.accentColor)
                        }
                    }
                }
            }
        }
    }

    private func addUserDelegation(_ u: MgmtUser) {
        let id = "print:user:\(u.id)"
        if settings.selectedTargetIds.contains(id) { return }
        let label: String = {
            let name = (u.name ?? "").trimmingCharacters(in: .whitespaces)
            let mail = (u.email ?? "").trimmingCharacters(in: .whitespaces)
            if !name.isEmpty && !mail.isEmpty { return "\(name) (\(mail))" }
            return name.isEmpty ? (mail.isEmpty ? u.id : mail) : name
        }()
        settings.targetLabels[id] = String(format: String(localized: "Delegation: %@"), label)
        settings.selectedTargetIds.append(id)
        settings.applyAutoResetPolicy()
        userQuery = ""
    }

    private func removeUserDelegation(_ id: String) {
        settings.selectedTargetIds.removeAll { $0 == id }
        settings.targetLabels.removeValue(forKey: id)
        settings.applyAutoResetPolicy()
    }

    @MainActor
    private func reloadMgmtUsers() async {
        guard let client = ApiClientFactory.make(baseURL: settings.serverURL,
                                                 token: settings.bearerToken) else {
            return
        }
        mgmtUsersLoading = true
        mgmtUsersError = ""
        defer { mgmtUsersLoading = false }
        do {
            let resp = try await client.managementUsers()
            allMgmtUsers = resp.users
            mgmtUsersLoaded = true
        } catch {
            // 403 fuer Employees ist OK — wir zeigen einen freundlichen Hinweis.
            mgmtUsersError = String(localized: "Benutzersuche nicht verfügbar (nur Admin/User).")
            mgmtUsersLoaded = true
        }
    }

    private var accountSummary: String? {
        let e = settings.userEmail.trimmingCharacters(in: .whitespaces)
        let n = settings.userFullName.trimmingCharacters(in: .whitespaces)
        if e.isEmpty && n.isEmpty { return nil }
        return n.isEmpty ? e : "\(n) · \(e)"
    }

    @ViewBuilder
    private func autoResetHint(now: Date) -> some View {
        if let expiry = settings.selectionExpiresAt {
            let remaining = max(0, Int(expiry.timeIntervalSince(now)))
            let mm = remaining / 60
            let ss = remaining % 60
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "clock.arrow.circlepath")
                    .foregroundColor(.orange)
                VStack(alignment: .leading, spacing: 2) {
                    Text(String(format: String(localized: "Auto-Reset in %d:%02d"), mm, ss))
                        .fontWeight(.medium)
                    Text(String(localized: "auto_reset_caption"))
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }
        }
    }

    private func toggle(_ t: Target) {
        if let idx = settings.selectedTargetIds.firstIndex(of: t.id) {
            settings.selectedTargetIds.remove(at: idx)
        } else {
            settings.selectedTargetIds.append(t.id)
        }
        // Auto-Reset-Timer nachziehen: abweichende Auswahl → 10-min-
        // Timer starten, Rueckkehr zum Default → Timer loeschen.
        settings.applyAutoResetPolicy()
    }

    /// Server liefert seit v0.5.0 das aufgeloeste Queue-Label direkt
    /// (z.B. "Anywhere - Marketing", "Office EG", ...). Wir uebernehmen
    /// das 1:1 — die alte Mapping-Logik mit "Mein Secure Print" als
    /// Fallback hat das echte Queue-Label vom Server ueberschrieben.
    /// Fallback nur fuer den Edge-Case dass der Server (alte Version)
    /// noch das hardcodierte deutsche Label liefert.
    private func localizedTargetLabel(_ t: Target) -> String {
        let label = t.label.trimmingCharacters(in: .whitespaces)
        if !label.isEmpty && label != "Mein Secure Print" {
            return label
        }
        // Fallback: alter Server liefert nur den Typ. Wir mappen lokal.
        switch t.type {
        case "print_secure":
            return String(localized: "Mein Secure Print")
        case "print_delegate":
            let name = t.label.replacingOccurrences(of: "Delegate: ", with: "")
            return String(format: String(localized: "Delegate: %@"), name)
        default:
            return t.label
        }
    }

    @MainActor
    private func reload() async {
        error = ""
        guard let client = ApiClientFactory.make(baseURL: settings.serverURL,
                                                 token: settings.bearerToken) else {
            error = String(localized: "Keine gültige Server-Konfiguration.")
            return
        }
        loading = true
        defer { loading = false }
        do {
            let resp = try await client.targetsFull()
            let all = resp.targets
            userCanChoose = resp.userCanChoose ?? false
            // Delegate-Ziele nur zeigen wenn der User das in den
            // Einstellungen freigeschaltet hat — Default ist OFF, damit
            // niemand versehentlich an einen anderen User druckt.
            let visible: [Target]
            if settings.delegateEnabled {
                visible = all
            } else {
                visible = all.filter { $0.type != "print_delegate" }
            }
            targets = visible
            // Falls eine aktuell ausgewaehlte Ziel-Id durch das Filtern
            // weggefallen ist (z.B. Delegate-Toggle gerade ausgeschaltet),
            // entfernen wir sie aus der Auswahl, damit der Upload nicht
            // ins Leere zielt.
            let allowedIds = Set(visible.map { $0.id })
            // v0.6.7: "print:queue:<id>" und "print:user:<id>" sind nicht in
            // `visible` enthalten (kommen aus dem Queue-/Delegation-Picker)
            // — die nicht prunen.
            let pruned = settings.selectedTargetIds.filter {
                allowedIds.contains($0)
                    || $0.hasPrefix("print:queue:")
                    || $0.hasPrefix("print:user:")
            }
            if pruned != settings.selectedTargetIds {
                settings.selectedTargetIds = pruned
            }
            // Label-Cache aktualisieren, damit UploadView die
            // Anzeigenamen statt nur die IDs rendert.
            // WICHTIG: print:queue: und print:user: Labels erhalten — die
            // werden vom Queue-/Delegation-Picker geschrieben und sind nicht
            // in `visible` enthalten. Nur alte Ziel-Labels aktualisieren.
            var labels: [String: String] = settings.targetLabels.filter {
                $0.key.hasPrefix("print:queue:") || $0.key.hasPrefix("print:user:")
            }
            for t in visible { labels[t.id] = localizedTargetLabel(t) }
            settings.targetLabels = labels
            // Falls noch nichts ausgewaehlt ist: erstes Ziel als Default
            // setzen, damit der Upload-Button nicht sofort disabled ist.
            if settings.selectedTargetIds.isEmpty, let first = visible.first {
                settings.selectedTargetIds = [first.id]
            }
        } catch {
            self.error = error.localizedDescription
        }
    }
}
