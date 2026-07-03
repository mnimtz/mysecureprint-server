import SwiftUI
import PrintixSendCore

/// Zeigt die Liste der verfügbaren Druck-Ziele (Queues/Printer aus
/// Printix). Auswahl speichert die Target-Id im SettingsStore, damit
/// UploadView + Share-Extension das gleiche Default-Ziel verwenden.
struct TargetsView: View {

    @EnvironmentObject private var settings: SettingsStore
    @EnvironmentObject private var cache: AppCache

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

    // Phase B: 2-Ebenen Queue-Picker
    @State private var userCanChoose: Bool = false
    @State private var showQueueSearchSheet: Bool = false
    @State private var allQueues: [QueueItem] = []
    @State private var queuesLoading: Bool = false
    @State private var queuesError: String = ""
    @State private var anywhereOnly: Bool = false

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
                    if cache.isSyncing && cache.targets.isEmpty {
                        HStack { ProgressView(); Text(String(localized: "targets_loading")) }
                    } else if cache.targets.isEmpty {
                        Text(String(localized: "targets_empty"))
                            .foregroundColor(.secondary)
                    } else {
                        ForEach(cache.targets) { t in
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
                                            .foregroundColor(MSP.cyan)
                                    } else {
                                        Image(systemName: "circle")
                                            .foregroundColor(.secondary)
                                    }
                                }
                            }
                        }
                    }
                }

                if cache.userCanChoose {
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
            .brandNavStyle(title: String(localized: "targets_nav_title"))
            .tint(MSP.cyan)
            .listStyle(.insetGrouped)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button {
                        Task {
                            await cache.refresh(settings: settings)
                            if settings.delegateEnabled { await reloadMgmtUsers() }
                        }
                    } label: {
                        if cache.isSyncing {
                            ProgressView().scaleEffect(0.8)
                        } else {
                            Image(systemName: "arrow.clockwise")
                        }
                    }
                    .disabled(cache.isSyncing)
                }
            }
            .refreshable {
                await cache.refresh(settings: settings)
                if settings.delegateEnabled { await reloadMgmtUsers() }
            }
            .task {
                await cache.preloadIfNeeded(settings: settings)
                if settings.delegateEnabled && !mgmtUsersLoaded { await reloadMgmtUsers() }
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

    private var selectedQueueTargetIds: [String] {
        settings.selectedTargetIds.filter { $0.hasPrefix("print:queue:") }
    }

    private var recentQueues: [QueueItem] {
        let selected = Set(selectedQueueTargetIds)
        return settings.recentQueueIds.compactMap { id in
            guard !selected.contains(id) else { return nil }
            return cache.queues.first { $0.id == id }
        }
    }

    @ViewBuilder
    private var queuePickerSection: some View {
        Section {
            // Header: Titel + Anywhere-Toggle
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(String(localized: "queue_picker_title"))
                        .font(.headline)
                    Text(String(localized: "queue_picker_sub"))
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                Spacer()
                // Anywhere-Filter-Chip
                Button {
                    anywhereOnly.toggle()
                } label: {
                    Text("Anywhere")
                        .font(.caption)
                        .fontWeight(.medium)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 4)
                        .background(anywhereOnly ? MSP.cyan : Color(.tertiarySystemFill))
                        .foregroundColor(anywhereOnly ? .white : .secondary)
                        .clipShape(Capsule())
                }
                .buttonStyle(.plain)
            }
            .padding(.vertical, 2)

            // Bereits ausgewählte Queues mit Entfernen-X
            ForEach(selectedQueueTargetIds, id: \.self) { id in
                HStack {
                    Image(systemName: "printer.dotmatrix")
                        .foregroundColor(MSP.cyan)
                    Text(settings.targetLabels[id] ?? id)
                        .font(.body)
                    Spacer()
                    Button { removeQueueSelection(id) } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundColor(.secondary)
                    }
                    .buttonStyle(.plain)
                }
            }

            // Zuletzt verwendet (Ebene 1)
            if !recentQueues.isEmpty {
                let visibleRecent = anywhereOnly
                    ? recentQueues.filter { $0.isAnywhere == true }
                    : recentQueues
                if !visibleRecent.isEmpty {
                    Text(String(localized: "Zuletzt verwendet"))
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .padding(.top, 4)
                    ForEach(visibleRecent, id: \.id) { q in
                        Button { addQueueSelection(q) } label: {
                            queueRow(q, icon: "clock")
                        }
                    }
                }
            }

            // Alle durchsuchen → Sub-Sheet (Ebene 2)
            Button {
                showQueueSearchSheet = true
                if cache.queues.isEmpty { Task { await cache.refresh(settings: settings) } }
            } label: {
                HStack {
                    Image(systemName: "magnifyingglass")
                        .foregroundColor(MSP.cyan)
                        .frame(width: 22)
                    Text(String(localized: "Alle Queues durchsuchen"))
                        .foregroundColor(.primary)
                    Spacer()
                    Image(systemName: "chevron.right")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundColor(Color(.tertiaryLabel))
                }
            }
        }
        .sheet(isPresented: $showQueueSearchSheet) {
            QueueSearchSheet(
                allQueues: cache.queues,
                selectedIds: Set(selectedQueueTargetIds),
                loading: cache.isSyncing,
                error: queuesError,
                anywhereOnly: $anywhereOnly
            ) { q in
                addQueueSelection(q)
                showQueueSearchSheet = false
            }
            .environmentObject(settings)
        }
    }

    @ViewBuilder
    private func queueRow(_ q: QueueItem, icon: String = "printer.dotmatrix") -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 4) {
                    Text(q.queueName).foregroundColor(.primary)
                    if q.isAnywhere == true {
                        Text("Anywhere")
                            .font(.caption2)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 1)
                            .background(MSP.cyan.opacity(0.15))
                            .foregroundColor(MSP.cyan)
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
                .foregroundColor(MSP.cyan)
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
        settings.addRecentQueue(id: q.id)
        settings.applyAutoResetPolicy()
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
                        .foregroundColor(MSP.cyan)
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
                                .foregroundColor(MSP.cyan)
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
    private func reloadQueues() async {
        guard let client = ApiClientFactory.make(baseURL: settings.serverURL,
                                                 token: settings.bearerToken) else { return }
        do {
            let resp = try await client.listQueues()
            cache.queues = resp.queues
        } catch {
            queuesError = String(localized: "Queues konnten nicht geladen werden.")
        }
    }
}

// MARK: - Phase B: Queue-Suche Sub-Sheet (Ebene 2)

private struct QueueSearchSheet: View {
    let allQueues: [QueueItem]
    let selectedIds: Set<String>
    let loading: Bool
    let error: String
    @Binding var anywhereOnly: Bool
    let onSelect: (QueueItem) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var query: String = ""
    @FocusState private var searchFocused: Bool

    private var filtered: [QueueItem] {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return allQueues.filter { item in
            if selectedIds.contains(item.id) { return false }
            if anywhereOnly && item.isAnywhere != true { return false }
            if q.isEmpty { return true }
            let name    = item.queueName.lowercased()
            let printer = (item.printerName ?? "").lowercased()
            let loc     = (item.location ?? "").lowercased()
            return name.contains(q) || printer.contains(q) || loc.contains(q)
        }
    }

    var body: some View {
        NavigationStack {
            List {
                // Anywhere-Filter-Chip
                Section {
                    Button {
                        anywhereOnly.toggle()
                    } label: {
                        HStack {
                            Text("Nur Anywhere-Queues")
                                .foregroundColor(.primary)
                            Spacer()
                            if anywhereOnly {
                                Image(systemName: "checkmark")
                                    .foregroundColor(MSP.cyan)
                            }
                        }
                    }
                }

                if loading && allQueues.isEmpty {
                    Section {
                        HStack { ProgressView(); Text(String(localized: "queues_loading")) }
                    }
                } else if !error.isEmpty {
                    Section {
                        Text(error).font(.caption).foregroundColor(.orange)
                    }
                } else if filtered.isEmpty {
                    Section {
                        Text(query.isEmpty
                             ? String(localized: "queues_empty")
                             : String(localized: "Keine Treffer."))
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                } else {
                    Section {
                        ForEach(filtered.prefix(50), id: \.id) { q in
                            Button {
                                onSelect(q)
                            } label: {
                                HStack {
                                    VStack(alignment: .leading, spacing: 2) {
                                        HStack(spacing: 4) {
                                            Text(q.queueName).foregroundColor(.primary)
                                            if q.isAnywhere == true {
                                                Text("Anywhere")
                                                    .font(.caption2)
                                                    .padding(.horizontal, 6)
                                                    .padding(.vertical, 1)
                                                    .background(MSP.cyan.opacity(0.15))
                                                    .foregroundColor(MSP.cyan)
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
                                        .foregroundColor(MSP.cyan)
                                }
                            }
                        }
                    }
                }
            }
            .listStyle(.insetGrouped)
            .searchable(text: $query,
                        placement: .navigationBarDrawer(displayMode: .always),
                        prompt: String(localized: "Queue suchen (Name, Drucker, Standort)"))
            .navigationTitle(String(localized: "Queue wählen"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button(String(localized: "Abbrechen")) { dismiss() }
                }
            }
            .onAppear { searchFocused = true }
        }
    }
}
