import SwiftUI
import PrintixSendCore

// MARK: - Delegate-Teams Hauptansicht

struct DelegateTeamsView: View {

    @EnvironmentObject private var settings: SettingsStore
    @EnvironmentObject private var cache: AppCache

    @State private var showCreateSheet = false
    @State private var newGroupName = ""
    @State private var createError = ""
    @State private var creating = false
    @State private var loadError = ""
    @State private var loading = false

    var body: some View {
        List {
            if loading {
                HStack { ProgressView(); Text(String(localized: "Lade Teams …")) }
            } else if cache.delegateGroups.isEmpty {
                emptyState
            } else {
                ForEach(cache.delegateGroups) { group in
                    NavigationLink(destination: DelegateTeamDetailView(group: group)) {
                        groupRow(group)
                    }
                }
                .onDelete(perform: deleteGroups)
            }

            if !loadError.isEmpty {
                Text(loadError).foregroundColor(.red).font(.caption).textSelection(.enabled)
            }
        }
        .listStyle(.insetGrouped)
        .brandNavStyle(title: String(localized: "Delegate-Teams"))
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                Button {
                    newGroupName = ""
                    createError = ""
                    showCreateSheet = true
                } label: {
                    Image(systemName: "plus")
                }
            }
        }
        .task {
            if cache.delegateGroups.isEmpty { await reload() }
        }
        .refreshable { await reload() }
        .sheet(isPresented: $showCreateSheet) {
            createSheet
        }
    }

    @ViewBuilder
    private func groupRow(_ group: DelegateGroup) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(group.name).font(.body)
            Text(memberSummary(group))
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .padding(.vertical, 2)
    }

    private func memberSummary(_ g: DelegateGroup) -> String {
        if g.members.isEmpty { return String(localized: "Keine Mitglieder") }
        if g.members.count == 1 { return g.members[0].displayName }
        let names = g.members.prefix(2).map { $0.displayName }.joined(separator: ", ")
        let extra = g.members.count - 2
        return extra > 0 ? "\(names) +\(extra)" : names
    }

    @ViewBuilder
    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "person.3")
                .font(.system(size: 40))
                .foregroundColor(.secondary)
            Text(String(localized: "Noch keine Teams"))
                .font(.headline)
            Text(String(localized: "Teams fassen mehrere Delegate-Empfänger zusammen — zum Drucken in einem Schritt."))
                .font(.caption)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
        }
        .padding()
        .listRowBackground(Color.clear)
        .listRowSeparator(.hidden)
    }

    @ViewBuilder
    private var createSheet: some View {
        NavigationStack {
            Form {
                Section(String(localized: "Teamname")) {
                    TextField(String(localized: "z. B. Buchhaltung"), text: $newGroupName)
                        .autocorrectionDisabled()
                }
                if !createError.isEmpty {
                    Section { Text(createError).foregroundColor(.red).font(.caption) }
                }
            }
            .navigationTitle(String(localized: "Neues Team"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(String(localized: "Abbrechen")) { showCreateSheet = false }
                }
                ToolbarItem(placement: .confirmationAction) {
                    if creating {
                        ProgressView().scaleEffect(0.85)
                    } else {
                        Button(String(localized: "Erstellen")) {
                            Task { await createGroup() }
                        }
                        .disabled(newGroupName.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                }
            }
        }
    }

    private func createGroup() async {
        let name = newGroupName.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { return }
        guard let client = ApiClientFactory.make(
            baseURL: settings.serverURL, token: settings.bearerToken) else { return }
        creating = true
        defer { creating = false }
        do {
            let group = try await client.createDelegateGroup(name: name)
            cache.delegateGroups.append(group)
            showCreateSheet = false
        } catch {
            createError = error.localizedDescription
        }
    }

    private func deleteGroups(at offsets: IndexSet) {
        let toDelete = offsets.map { cache.delegateGroups[$0] }
        cache.delegateGroups.remove(atOffsets: offsets)
        Task {
            guard let client = ApiClientFactory.make(
                baseURL: settings.serverURL, token: settings.bearerToken) else { return }
            for g in toDelete {
                try? await client.deleteDelegateGroup(uuid: g.group_uuid)
            }
        }
    }

    private func reload() async {
        guard let client = ApiClientFactory.make(
            baseURL: settings.serverURL, token: settings.bearerToken) else { return }
        loading = true
        defer { loading = false }
        do {
            cache.delegateGroups = try await client.listDelegateGroups()
            loadError = ""
        } catch {
            loadError = error.localizedDescription
        }
    }
}

// MARK: - Team-Detailseite

struct DelegateTeamDetailView: View {

    @EnvironmentObject private var settings: SettingsStore
    @EnvironmentObject private var cache: AppCache

    let group: DelegateGroup

    @State private var localGroup: DelegateGroup
    @State private var showRenameSheet = false
    @State private var renameText = ""
    @State private var showAddMember = false
    @State private var memberQuery = ""
    @State private var allUsers: [MgmtUser] = []
    @State private var usersLoaded = false
    @State private var usersLoading = false
    @State private var saving = false
    @State private var error = ""

    init(group: DelegateGroup) {
        self.group = group
        _localGroup = State(initialValue: group)
    }

    var body: some View {
        List {
            // Members section
            Section(header: membersHeader) {
                if localGroup.members.isEmpty {
                    Text(String(localized: "Noch keine Mitglieder — tippe + um welche hinzuzufügen."))
                        .font(.caption)
                        .foregroundColor(.secondary)
                } else {
                    ForEach(localGroup.members) { member in
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(member.displayName).font(.body)
                                if member.member_display_name != member.member_email {
                                    Text(member.member_email).font(.caption).foregroundColor(.secondary)
                                }
                            }
                            Spacer()
                        }
                    }
                    .onDelete(perform: deleteMembers)
                }
            }

            if !error.isEmpty {
                Section { Text(error).foregroundColor(.red).font(.caption).textSelection(.enabled) }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle(localGroup.name)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                Menu {
                    Button {
                        renameText = localGroup.name
                        showRenameSheet = true
                    } label: {
                        Label(String(localized: "Umbenennen"), systemImage: "pencil")
                    }
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
            }
        }
        .sheet(isPresented: $showAddMember) {
            addMemberSheet
        }
        .sheet(isPresented: $showRenameSheet) {
            renameSheet
        }
        .task {
            if cache.mgmtUsers.isEmpty && !usersLoaded { await loadUsers() }
            else { allUsers = cache.mgmtUsers; usersLoaded = true }
        }
        .onDisappear {
            // Sync localGroup back to cache
            if let idx = cache.delegateGroups.firstIndex(where: { $0.id == localGroup.id }) {
                cache.delegateGroups[idx] = localGroup
            }
        }
    }

    @ViewBuilder
    private var membersHeader: some View {
        HStack {
            Text(String(localized: "Mitglieder (\(localGroup.members.count)/20)"))
            Spacer()
            Button {
                memberQuery = ""
                showAddMember = true
            } label: {
                Image(systemName: "plus.circle.fill")
                    .foregroundColor(MSP.cyan)
            }
            .buttonStyle(.plain)
            .disabled(localGroup.members.count >= 20)
        }
    }

    @ViewBuilder
    private var addMemberSheet: some View {
        NavigationStack {
            List {
                if usersLoading {
                    HStack { ProgressView(); Text(String(localized: "Lade Benutzer …")) }
                } else {
                    let filtered = filteredUsers
                    if filtered.isEmpty {
                        Text(String(localized: "Keine Ergebnisse")).foregroundColor(.secondary)
                    } else {
                        ForEach(filtered) { u in
                            Button { Task { await addMember(u) } } label: {
                                HStack {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(u.name ?? u.email ?? u.id).foregroundColor(.primary)
                                        if let e = u.email, !e.isEmpty {
                                            Text(e).font(.caption).foregroundColor(.secondary)
                                        }
                                    }
                                    Spacer()
                                    if saving { ProgressView().scaleEffect(0.8) }
                                    else { Image(systemName: "plus.circle").foregroundColor(MSP.cyan) }
                                }
                            }
                            .disabled(saving || localGroup.members.contains(where: {
                                $0.member_email.lowercased() == (u.email ?? "").lowercased()
                            }))
                        }
                    }
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle(String(localized: "Mitglied hinzufügen"))
            .navigationBarTitleDisplayMode(.inline)
            .searchable(text: $memberQuery, placement: .navigationBarDrawer(displayMode: .always))
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(String(localized: "Fertig")) { showAddMember = false }
                }
            }
        }
    }

    @ViewBuilder
    private var renameSheet: some View {
        NavigationStack {
            Form {
                Section(String(localized: "Neuer Name")) {
                    TextField(localGroup.name, text: $renameText)
                        .autocorrectionDisabled()
                }
            }
            .navigationTitle(String(localized: "Team umbenennen"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(String(localized: "Abbrechen")) { showRenameSheet = false }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(String(localized: "Speichern")) {
                        Task { await renameGroup() }
                    }
                    .disabled(renameText.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
        }
    }

    private var filteredUsers: [MgmtUser] {
        let q = memberQuery.lowercased().trimmingCharacters(in: .whitespaces)
        if q.isEmpty { return allUsers }
        return allUsers.filter {
            ($0.name ?? "").lowercased().contains(q) ||
            ($0.email ?? "").lowercased().contains(q)
        }
    }

    private func loadUsers() async {
        guard let client = ApiClientFactory.make(
            baseURL: settings.serverURL, token: settings.bearerToken) else { return }
        usersLoading = true
        defer { usersLoading = false }
        if let resp = try? await client.managementUsers() {
            allUsers = resp.users
            cache.mgmtUsers = resp.users
        }
        usersLoaded = true
    }

    private func addMember(_ u: MgmtUser) async {
        guard let client = ApiClientFactory.make(
            baseURL: settings.serverURL, token: settings.bearerToken) else { return }
        let email = (u.email ?? "").trimmingCharacters(in: .whitespaces).lowercased()
        guard !email.isEmpty else { return }
        guard !localGroup.members.contains(where: { $0.member_email == email }) else { return }
        saving = true
        defer { saving = false }
        do {
            let name = (u.name ?? "").trimmingCharacters(in: .whitespaces)
            try await client.addGroupMember(
                groupUuid: localGroup.group_uuid,
                email: email,
                displayName: name,
                printixId: u.id
            )
            let newMember = DelegateGroupMember(
                member_email: email,
                member_display_name: name,
                member_printix_id: u.id
            )
            localGroup = DelegateGroup(
                group_uuid: localGroup.group_uuid,
                name: localGroup.name,
                created_at: localGroup.created_at,
                members: localGroup.members + [newMember]
            )
            error = ""
            showAddMember = false
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func deleteMembers(at offsets: IndexSet) {
        let toRemove = offsets.map { localGroup.members[$0] }
        var updated = localGroup.members
        updated.remove(atOffsets: offsets)
        localGroup = DelegateGroup(
            group_uuid: localGroup.group_uuid,
            name: localGroup.name,
            created_at: localGroup.created_at,
            members: updated
        )
        Task {
            guard let client = ApiClientFactory.make(
                baseURL: settings.serverURL, token: settings.bearerToken) else { return }
            for m in toRemove {
                try? await client.removeGroupMember(groupUuid: localGroup.group_uuid, email: m.member_email)
            }
        }
    }

    private func renameGroup() async {
        let name = renameText.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { return }
        guard let client = ApiClientFactory.make(
            baseURL: settings.serverURL, token: settings.bearerToken) else { return }
        do {
            try await client.renameDelegateGroup(uuid: localGroup.group_uuid, name: name)
            localGroup = DelegateGroup(
                group_uuid: localGroup.group_uuid,
                name: name,
                created_at: localGroup.created_at,
                members: localGroup.members
            )
            showRenameSheet = false
        } catch {
            self.error = error.localizedDescription
        }
    }
}
