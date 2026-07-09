import SwiftUI
import PrintixSendCore
import UniformTypeIdentifiers

// MARK: - Übersicht: alle eigenen AirPrint-Profile

struct AirPrintProfilesView: View {
    @EnvironmentObject private var settings: SettingsStore
    @EnvironmentObject private var cache: AppCache

    @State private var profiles: [AirprintProfile] = []
    @State private var companyDefault: AirprintCompanyDefault? = nil
    @State private var loading = false
    @State private var installingCompany = false
    @State private var error: String = ""
    @State private var showWizard = false
    @State private var pendingDownload: URL? = nil
    @State private var revokingIds: Set<String> = []

    var body: some View {
        List {
            Section {
                Text(String(localized: "airprint_intro"))
                    .font(.footnote)
                    .foregroundColor(.secondary)
            }

            // ── 1) Firmen SecurePrint ──────────────────────────────
            companyDefaultSection

            // ── 2) Direkt-Drucker ──────────────────────────────────
            directPrinterSection

            // ── Installierte Profile (falls vorhanden) ─────────────
            if !profiles.isEmpty {
                Section(String(localized: "airprint_installed_section")) {
                    ForEach(profiles) { p in
                        profileRow(p)
                    }
                }
            } else if loading {
                Section {
                    HStack {
                        ProgressView()
                        Text(String(localized: "airprint_loading"))
                    }
                }
            }

            if !error.isEmpty {
                Section(String(localized: "airprint_error_section")) {
                    Text(error)
                        .foregroundColor(MSP.danger)
                        .font(.caption)
                        .textSelection(.enabled)
                }
            }
        }
        .listStyle(.insetGrouped)
        .brandNavStyle(title: String(localized: "airprint_view_title"))
        .refreshable { await reload() }
        .task { await reload() }
        .sheet(isPresented: $showWizard) {
            AirPrintNewProfileView(onCreated: { newProfile, mobileConfigURL in
                Task {
                    await reload()
                    pendingDownload = mobileConfigURL
                }
            })
        }
        .sheet(item: Binding(
            get: { pendingDownload.map { InstallSheetURL(url: $0) } },
            set: { pendingDownload = $0?.url })) { wrap in
            AirPrintInstallSheet(mobileconfigURL: wrap.url)
        }
    }

    // MARK: - Firmen SecurePrint Section

    @ViewBuilder
    private var companyDefaultSection: some View {
        Section {
            if let cd = companyDefault, cd.configured {
                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 10) {
                        Image(systemName: "building.2.fill")
                            .foregroundColor(MSP.cyan)
                            .frame(width: 24)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(cd.queueDisplayName)
                                .font(.subheadline).fontWeight(.semibold)
                            Text(String(localized: "airprint_company_hint"))
                                .font(.caption).foregroundColor(.secondary)
                        }
                    }
                    if !cd.existingProfileId.isEmpty {
                        // Bereits installiert
                        HStack {
                            Image(systemName: "checkmark.circle.fill")
                                .foregroundColor(.green)
                            Text(String(localized: "airprint_company_already_installed"))
                                .font(.caption)
                        }
                    } else {
                        Button {
                            Task { await installCompanyDefault() }
                        } label: {
                            HStack {
                                if installingCompany {
                                    ProgressView().scaleEffect(0.85)
                                } else {
                                    Image(systemName: "arrow.down.circle.fill")
                                }
                                Text(String(localized: "airprint_company_install_button"))
                                    .fontWeight(.semibold)
                            }
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 8)
                            .background(MSP.gold)
                            .foregroundColor(MSP.navy)
                            .cornerRadius(10)
                        }
                        .buttonStyle(.plain)
                        .disabled(installingCompany || loading)
                    }
                }
                .padding(.vertical, 4)
            } else {
                Text(String(localized: "airprint_company_not_configured"))
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        } header: {
            Text(String(localized: "airprint_company_section"))
        }
    }

    // MARK: - Direkt-Drucker Section

    @ViewBuilder
    private var directPrinterSection: some View {
        Section {
            Button {
                showWizard = true
            } label: {
                HStack {
                    Image(systemName: "plus.circle.fill")
                        .foregroundColor(MSP.cyan)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(String(localized: "airprint_direct_add_button"))
                            .fontWeight(.semibold)
                        Text(String(localized: "airprint_direct_hint"))
                            .font(.caption).foregroundColor(.secondary)
                    }
                }
            }
            .disabled(loading)
        } header: {
            Text(String(localized: "airprint_direct_section"))
        }
    }

    // MARK: - Actions

    @MainActor
    private func installCompanyDefault() async {
        guard let cd = companyDefault, cd.configured,
              let client = ApiClientFactory.make(baseURL: settings.serverURL,
                                                  token: settings.bearerToken) else { return }
        installingCompany = true
        defer { installingCompany = false }
        do {
            let resp = try await client.createAirprintProfile(
                printerId: cd.printerId,
                queueId: cd.queueId,
                queueDisplayName: cd.queueDisplayName,
                displayName: nil
            )
            let data = try await client.downloadAirprintProfile(profileId: resp.profileId)
            let dir = FileManager.default.temporaryDirectory
                .appendingPathComponent("airprint", isDirectory: true)
            try? FileManager.default.createDirectory(at: dir,
                                                      withIntermediateDirectories: true)
            let file = dir.appendingPathComponent("MySecurePrint.mobileconfig")
            try data.write(to: file, options: .atomic)
            await reload()
            pendingDownload = file
        } catch {
            self.error = error.localizedDescription
        }
    }

    @ViewBuilder
    private func profileRow(_ p: AirprintProfile) -> some View {
        HStack(spacing: 12) {
            Image(systemName: "printer.fill")
                .foregroundColor(MSP.cyan)
                .frame(width: 28)
            VStack(alignment: .leading, spacing: 3) {
                Text(p.queueDisplayName.isEmpty
                     ? String(localized: "airprint_unnamed_queue")
                     : p.queueDisplayName)
                    .font(.subheadline).fontWeight(.semibold)
                if let display = p.displayName, !display.isEmpty {
                    Text(display).font(.caption).foregroundColor(.secondary)
                }
                if let last = p.lastUsedAt, !last.isEmpty {
                    Text(String(format: String(localized: "airprint_last_used"), last))
                        .font(.caption2).foregroundColor(.secondary)
                } else {
                    Text(String(localized: "airprint_never_used"))
                        .font(.caption2).foregroundColor(.secondary)
                }
            }
            Spacer()
            if revokingIds.contains(p.id) {
                ProgressView().scaleEffect(0.8)
            }
        }
        .padding(.vertical, 4)
        .swipeActions(edge: .trailing) {
            Button(role: .destructive) {
                Task { await revoke(p) }
            } label: {
                Label(String(localized: "airprint_revoke"), systemImage: "trash")
            }
            .disabled(revokingIds.contains(p.id))
        }
    }

    @MainActor
    private func reload() async {
        guard let client = ApiClientFactory.make(baseURL: settings.serverURL,
                                                  token: settings.bearerToken) else { return }
        loading = true
        defer { loading = false }
        do {
            async let profilesTask = client.listAirprintProfiles()
            async let companyTask = client.getAirprintCompanyDefault()
            profiles = try await profilesTask
            do {
                companyDefault = try await companyTask
            } catch {
                // Firmen-Default ist optional — Fehler hier nicht als Haupt-Error zeigen
                companyDefault = nil
            }
            error = ""
        } catch {
            self.error = error.localizedDescription
        }
    }

    @MainActor
    private func revoke(_ p: AirprintProfile) async {
        guard let client = ApiClientFactory.make(baseURL: settings.serverURL,
                                                  token: settings.bearerToken) else { return }
        revokingIds.insert(p.id)
        defer { revokingIds.remove(p.id) }
        do {
            _ = try await client.revokeAirprintProfile(profileId: p.id)
            profiles.removeAll { $0.id == p.id }
        } catch {
            self.error = error.localizedDescription
        }
    }
}

// Kleine ID-wrapper damit .sheet(item:) mit URL geht
private struct InstallSheetURL: Identifiable {
    let url: URL
    var id: String { url.absoluteString }
}


// MARK: - Wizard: neues Profil erstellen

struct AirPrintNewProfileView: View {
    @EnvironmentObject private var settings: SettingsStore
    @EnvironmentObject private var cache: AppCache
    @Environment(\.dismiss) private var dismiss

    var onCreated: (AirprintCreateResponse, URL) -> Void

    @State private var selectedQueueId: String = ""
    @State private var displayName: String = ""
    @State private var creating = false
    @State private var error: String = ""

    private var availableQueues: [QueueItem] {
        cache.queues
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text(String(localized: "airprint_wizard_intro"))
                        .font(.footnote)
                        .foregroundColor(.secondary)
                }

                Section(String(localized: "airprint_wizard_queue_section")) {
                    if availableQueues.isEmpty {
                        Text(String(localized: "airprint_wizard_no_queues"))
                            .font(.caption)
                            .foregroundColor(.secondary)
                    } else {
                        Picker(String(localized: "airprint_wizard_queue_picker"),
                               selection: $selectedQueueId) {
                            Text(String(localized: "airprint_wizard_queue_none"))
                                .tag("")
                            ForEach(availableQueues) { q in
                                Text(q.queueName).tag(q.queueId)
                            }
                        }
                        .pickerStyle(.navigationLink)
                    }
                }

                Section {
                    TextField(String(localized: "airprint_wizard_display_name_placeholder"),
                              text: $displayName)
                        .autocorrectionDisabled()
                } header: {
                    Text(String(localized: "airprint_wizard_display_name_header"))
                } footer: {
                    Text(String(localized: "airprint_wizard_display_name_footer"))
                        .font(.caption2)
                }

                if !error.isEmpty {
                    Section {
                        Text(error)
                            .foregroundColor(MSP.danger)
                            .font(.caption)
                            .textSelection(.enabled)
                    }
                }
            }
            .brandNavStyle(title: String(localized: "airprint_wizard_title"))
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(String(localized: "airprint_wizard_cancel")) { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        Task { await create() }
                    } label: {
                        if creating {
                            ProgressView()
                        } else {
                            Text(String(localized: "airprint_wizard_create"))
                        }
                    }
                    .disabled(creating || selectedQueueId.isEmpty)
                }
            }
        }
    }

    @MainActor
    private func create() async {
        guard let client = ApiClientFactory.make(baseURL: settings.serverURL,
                                                  token: settings.bearerToken) else { return }
        guard let queue = availableQueues.first(where: { $0.queueId == selectedQueueId }) else {
            error = String(localized: "airprint_wizard_queue_not_found")
            return
        }
        creating = true
        defer { creating = false }
        do {
            let resp = try await client.createAirprintProfile(
                printerId: queue.printerId ?? "",
                queueId: queue.queueId,
                queueDisplayName: queue.queueName,
                displayName: displayName.trimmingCharacters(in: .whitespaces).isEmpty
                    ? nil
                    : displayName.trimmingCharacters(in: .whitespaces)
            )

            // Datei jetzt runterladen und in einen temporären Ordner legen,
            // damit iOS beim UIDocumentInteractionController-Öffnen den
            // Profile-Installations-Dialog anzeigt.
            let data = try await client.downloadAirprintProfile(profileId: resp.profileId)
            let dir = FileManager.default.temporaryDirectory
                .appendingPathComponent("airprint", isDirectory: true)
            try? FileManager.default.createDirectory(at: dir,
                                                      withIntermediateDirectories: true)
            let file = dir.appendingPathComponent("MySecurePrint.mobileconfig")
            try data.write(to: file, options: .atomic)

            dismiss()
            onCreated(resp, file)
        } catch {
            self.error = error.localizedDescription
        }
    }
}


// MARK: - Sheet nach Profil-Erstellung: an iOS zur Installation übergeben

/// iOS erlaubt Profile nur via Safari + AppleSchemes vollständig zu installieren.
/// Bester Weg für die App: den Nutzer via `UIApplication.open` zur URL leiten —
/// iOS erkennt `application/x-apple-aspen-config` und öffnet den Install-Dialog.
struct AirPrintInstallSheet: View {
    @Environment(\.dismiss) private var dismiss
    let mobileconfigURL: URL
    @State private var showShare = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 18) {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.system(size: 56))
                        .foregroundColor(.green)
                        .padding(.top, 20)

                    Text(String(localized: "airprint_install_headline"))
                        .font(.title2).fontWeight(.bold)

                    Text(String(localized: "airprint_install_body"))
                        .font(.body)
                        .foregroundColor(.secondary)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 20)

                    VStack(alignment: .leading, spacing: 12) {
                        stepRow(number: "1", text: String(localized: "airprint_install_step1_v2"))
                        stepRow(number: "2", text: String(localized: "airprint_install_step2_v2"))
                        stepRow(number: "3", text: String(localized: "airprint_install_step3_v2"))
                    }
                    .padding(.horizontal, 30)

                    // Fallback-Hinweis — wenn's mal nicht automatisch geht.
                    VStack(alignment: .leading, spacing: 8) {
                        Text(String(localized: "airprint_install_fallback_title"))
                            .font(.subheadline).fontWeight(.semibold)
                            .foregroundColor(MSP.navy)
                        Text(String(localized: "airprint_install_fallback_path1"))
                            .font(.callout)
                        Text(String(localized: "airprint_install_fallback_path2"))
                            .font(.callout)
                        Text(String(localized: "airprint_install_fallback_hint"))
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    .padding(14)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.yellow.opacity(0.12))
                    .cornerRadius(10)
                    .padding(.horizontal, 20)

                    Button {
                        showShare = true
                    } label: {
                        Text(String(localized: "airprint_install_open_button"))
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 14)
                            .background(MSP.gold)
                            .foregroundColor(MSP.navy)
                            .cornerRadius(14)
                    }
                    .padding(.horizontal, 20)
                    .padding(.top, 8)

                    Button(String(localized: "airprint_install_close")) {
                        dismiss()
                    }
                    .foregroundColor(.secondary)
                    .padding(.bottom, 20)
                }
            }
            .brandNavStyle(title: String(localized: "airprint_install_title"))
            .sheet(isPresented: $showShare) {
                ActivityShareSheet(activityItems: [mobileconfigURL])
            }
        }
    }

    @ViewBuilder
    private func stepRow(number: String, text: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Text(number)
                .font(.system(size: 13, weight: .bold))
                .foregroundColor(.white)
                .frame(width: 22, height: 22)
                .background(MSP.cyan)
                .clipShape(Circle())
            Text(text)
                .font(.callout)
        }
    }
}

// System-Share-Sheet — der zuverlaessige Weg fuer .mobileconfig auf
// modernen iOS-Versionen. User waehlt "In Dateien sichern" oder
// AirDrop; iOS erkennt die Datei-Endung und startet den Profil-
// Install-Flow.
struct ActivityShareSheet: UIViewControllerRepresentable {
    let activityItems: [Any]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: activityItems,
                                  applicationActivities: nil)
    }

    func updateUIViewController(_ vc: UIActivityViewController, context: Context) {}
}
