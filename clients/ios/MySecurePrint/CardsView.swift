import SwiftUI
import PrintixSendCore

/// Karten-Tab — Self-Service fuer RFID-Karten des angemeldeten Users.
///
/// Scope v1: nur eigene Karten. Der User sieht was ueber seine Karten in
/// der Server-DB steht, kann sie loeschen, und neue Karten anlegen —
/// entweder manuell per Tastatureingabe oder (v2) via iPhone-NFC-Scan.
///
/// Sichtbarkeit: nur Rollen admin/user, analog zum Management-Tab. Das
/// Gate sitzt in ContentView → MainTabs, nicht hier.
struct CardsView: View {
    @EnvironmentObject private var settings: SettingsStore

    @State private var cards: [Card] = []
    @State private var profiles: [CardProfile] = []
    @State private var defaultProfileId: String = ""
    @State private var isLoading = false
    @State private var errorMessage: String?
    @State private var showAdd = false

    var body: some View {
        NavigationStack {
            Group {
                if cards.isEmpty && !isLoading && errorMessage == nil {
                    emptyState
                } else {
                    cardList
                }
            }
            .navigationTitle("Karten")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showAdd = true
                    } label: {
                        Label("Neue Karte", systemImage: "plus.circle.fill")
                    }
                    .disabled(isLoading)
                }
            }
            .refreshable { await reload() }
            .task { await reload() }
            .sheet(isPresented: $showAdd) {
                AddCardView(
                    profiles: profiles,
                    defaultProfileId: defaultProfileId
                ) { newCard in
                    cards.insert(newCard, at: 0)
                }
            }
        }
    }

    // MARK: - Sub-views

    @ViewBuilder
    private var cardList: some View {
        List {
            if let err = errorMessage {
                Section {
                    Label(err, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.red)
                        .font(.footnote)
                }
            }
            Section {
                ForEach(cards) { card in
                    NavigationLink {
                        CardDetailView(card: card) { deletedId in
                            cards.removeAll { $0.id == deletedId }
                        }
                    } label: {
                        CardRow(card: card)
                    }
                }
            } header: {
                Text("\(cards.count) Karte(n)")
            }
        }
    }

    @ViewBuilder
    private var emptyState: some View {
        // In einen ScrollView eingebettet, damit .refreshable auch dann
        // triggert, wenn noch keine Karten da sind.
        ScrollView {
            VStack(spacing: 16) {
                Image(systemName: "creditcard")
                    .font(.system(size: 48))
                    .foregroundStyle(.secondary)
                if let err = errorMessage {
                    Text(err)
                        .foregroundStyle(.red)
                        .multilineTextAlignment(.center)
                        .font(.footnote)
                        .padding(.horizontal)
                } else if isLoading {
                    ProgressView()
                } else {
                    Text("Noch keine Karten hinterlegt.")
                        .foregroundStyle(.secondary)
                    Button {
                        showAdd = true
                    } label: {
                        Label("Erste Karte anlegen", systemImage: "plus.circle.fill")
                    }
                    .buttonStyle(.borderedProminent)
                }
            }
            .frame(maxWidth: .infinity)
            .padding(.top, 80)
        }
    }

    // MARK: - Loading

    private func reload() async {
        guard let base = settings.serverBaseURL,
              let client = ApiClientFactory.make(baseURL: base.absoluteString,
                                                 token: settings.bearerToken) else {
            await MainActor.run {
                errorMessage = String(localized: "Kein Server konfiguriert.")
            }
            return
        }
        await MainActor.run {
            isLoading = true
            errorMessage = nil
        }
        do {
            async let cardsFuture  = client.listCards()
            async let profsFuture  = client.listCardProfilesWithDefault()
            let (c, pd) = try await (cardsFuture, profsFuture)
            await MainActor.run {
                self.cards = c
                self.profiles = pd.0
                self.defaultProfileId = pd.1
                self.isLoading = false
            }
        } catch {
            await MainActor.run {
                self.errorMessage = error.localizedDescription
                self.isLoading = false
            }
        }
    }
}


// MARK: - Card Row

private struct CardRow: View {
    let card: Card

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(card.localValue.isEmpty ? String(localized: "(leer)") : card.localValue)
                    .font(.system(.body, design: .monospaced))
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer()
                if !card.profileVendor.isEmpty {
                    Text(card.profileVendor)
                        .font(.caption)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Color.secondary.opacity(0.15))
                        .clipShape(Capsule())
                }
            }
            if !card.profileName.isEmpty {
                Text(card.profileName)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 2)
    }
}


// MARK: - Card Detail

private struct CardDetailView: View {
    let card: Card
    let onDelete: (Int) -> Void

    @EnvironmentObject private var settings: SettingsStore
    @Environment(\.dismiss) private var dismiss

    @State private var isDeleting = false
    @State private var errorMessage: String?
    @State private var confirmDelete = false

    var body: some View {
        Form {
            Section("Profil") {
                if !card.profileName.isEmpty {
                    row("Name", card.profileName)
                }
                if !card.profileVendor.isEmpty {
                    row("Hersteller", card.profileVendor)
                }
                if !card.profileReaderModel.isEmpty {
                    row("Lesegeraet", card.profileReaderModel)
                }
                if card.profileName.isEmpty && card.profileVendor.isEmpty {
                    Text("Ohne Profil")
                        .foregroundStyle(.secondary)
                }
            }

            Section("Werte") {
                if !card.localValue.isEmpty {
                    monoRow("Lokal", card.localValue)
                }
                if !card.preview.hex.isEmpty {
                    monoRow("Hex", card.preview.hex)
                }
                if !card.preview.hexReversed.isEmpty {
                    monoRow("Hex (umgekehrt)", card.preview.hexReversed)
                }
                if !card.preview.decimal.isEmpty {
                    monoRow("Dezimal", card.preview.decimal)
                }
                if !card.preview.decimalReversed.isEmpty {
                    monoRow("Dezimal (umgekehrt)", card.preview.decimalReversed)
                }
                if !card.preview.base64Text.isEmpty {
                    monoRow("Base64", card.preview.base64Text)
                }
                if !card.preview.finalSubmitValue.isEmpty {
                    monoRow("An Printix gesendet", card.preview.finalSubmitValue)
                }
            }

            Section("Printix") {
                if !card.printixCardId.isEmpty {
                    monoRow("Card-ID", card.printixCardId)
                }
                if !card.source.isEmpty {
                    row("Quelle", card.source)
                }
                if !card.notes.isEmpty {
                    row("Notiz", card.notes)
                }
                if !card.createdAt.isEmpty {
                    row("Angelegt", card.createdAt)
                }
                if !card.updatedAt.isEmpty && card.updatedAt != card.createdAt {
                    row("Geaendert", card.updatedAt)
                }
            }

            if let err = errorMessage {
                Section {
                    Label(err, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.red)
                        .font(.footnote)
                }
            }

            Section {
                Button(role: .destructive) {
                    confirmDelete = true
                } label: {
                    if isDeleting {
                        HStack {
                            ProgressView()
                            Text("Loesche…")
                        }
                    } else {
                        Label("Karte loeschen", systemImage: "trash")
                    }
                }
                .disabled(isDeleting)
            }
        }
        .navigationTitle("Karten-Details")
        .navigationBarTitleDisplayMode(.inline)
        .confirmationDialog("Karte wirklich loeschen?",
                            isPresented: $confirmDelete,
                            titleVisibility: .visible) {
            Button("Loeschen", role: .destructive) {
                Task { await delete() }
            }
            Button("Abbrechen", role: .cancel) { }
        } message: {
            Text("Die Karte wird bei Printix und lokal entfernt. Druckfreigabe am Geraet funktioniert danach nicht mehr.")
        }
    }

    @ViewBuilder
    private func row(_ label: LocalizedStringKey, _ value: String) -> some View {
        HStack {
            Text(label)
            Spacer()
            Text(value)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.trailing)
        }
    }

    @ViewBuilder
    private func monoRow(_ label: LocalizedStringKey, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.system(.footnote, design: .monospaced))
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func delete() async {
        guard let base = settings.serverBaseURL,
              let client = ApiClientFactory.make(baseURL: base.absoluteString,
                                                 token: settings.bearerToken) else {
            errorMessage = String(localized: "Kein Server konfiguriert.")
            return
        }
        await MainActor.run {
            isDeleting = true
            errorMessage = nil
        }
        do {
            try await client.deleteCard(id: card.id)
            await MainActor.run {
                onDelete(card.id)
                isDeleting = false
                dismiss()
            }
        } catch {
            await MainActor.run {
                errorMessage = error.localizedDescription
                isDeleting = false
            }
        }
    }
}


// MARK: - Add Card

private struct AddCardView: View {
    let profiles: [CardProfile]
    /// Wenn gesetzt, zeigt das Sheet KEINEN Profil-Picker mehr, sondern
    /// eine Read-only-Anzeige ("Firmen-Standard"). Der Wert wird still
    /// beim Speichern mitgeschickt. Kommt vom Server-Endpoint
    /// /desktop/cards/profiles → default_profile_id.
    let defaultProfileId: String
    let onCreated: (Card) -> Void

    @EnvironmentObject private var settings: SettingsStore
    @Environment(\.dismiss) private var dismiss

    @State private var rawValue: String = ""
    @State private var selectedProfileId: String = ""
    @State private var notes: String = ""
    @State private var preview: CardPreview?
    @State private var isPreviewing = false
    @State private var isSaving = false
    @State private var isScanning = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("Kartenwert") {
                    TextField("Roh-Wert (Hex, Dezimal oder Text)",
                              text: $rawValue,
                              axis: .vertical)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                        .font(.system(.body, design: .monospaced))

                    // NFC-Scan: Core NFC Session oeffnen, UID als Hex in
                    // das rawValue-Feld schreiben. Funktioniert nur auf
                    // physischem iPhone (Simulator: NFCError.notAvailable).
                    Button {
                        Task { await scanNFC() }
                    } label: {
                        if isScanning {
                            HStack {
                                ProgressView()
                                Text("Scanne…")
                            }
                        } else {
                            Label("Mit iPhone scannen", systemImage: "wave.3.right.circle")
                        }
                    }
                    .disabled(isScanning || isSaving)
                }

                // Transformation: wenn der Admin einen Firmen-Standard
                // gesetzt hat, zeigen wir nur den gewaehlten Namen (read-
                // only) mit Company-Policy-Hinweis — kein Picker, kein
                // Dropdown. So muss der Mitarbeiter nichts auswaehlen und
                // kann auch nichts "falsch" machen.
                Section("Transformation") {
                    if !defaultProfileId.isEmpty,
                       let fixed = profiles.first(where: { $0.id == defaultProfileId }) {
                        HStack {
                            Text("Profil")
                            Spacer()
                            Text(profileLabel(fixed))
                                .foregroundStyle(.secondary)
                                .multilineTextAlignment(.trailing)
                        }
                        Label {
                            Text("Durch Firmenrichtlinie festgelegt.")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        } icon: {
                            Image(systemName: "lock.shield")
                                .foregroundStyle(.secondary)
                        }
                    } else {
                        Picker("Profil", selection: $selectedProfileId) {
                            Text("Ohne Profil").tag("")
                            ForEach(groupedProfiles, id: \.key) { group in
                                Section(LocalizedStringKey(group.key)) {
                                    ForEach(group.profiles) { p in
                                        Text(profileLabel(p)).tag(p.id)
                                    }
                                }
                            }
                        }
                        .pickerStyle(.navigationLink)
                    }
                }

                Section("Notiz (optional)") {
                    TextField("z.B. Dienstausweis, Mitarbeiter-Nr. …",
                              text: $notes)
                }

                if let p = preview {
                    Section("Vorschau") {
                        if !p.hex.isEmpty {
                            monoRow("Hex", p.hex)
                        }
                        if !p.decimal.isEmpty {
                            monoRow("Dezimal", p.decimal)
                        }
                        if !p.base64Text.isEmpty {
                            monoRow("Base64", p.base64Text)
                        }
                        if !p.finalSubmitValue.isEmpty {
                            monoRow("An Printix", p.finalSubmitValue)
                        }
                    }
                }

                if let err = errorMessage {
                    Section {
                        Label(err, systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(.red)
                            .font(.footnote)
                    }
                }
            }
            .navigationTitle("Neue Karte")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Abbrechen") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        Task { await save() }
                    } label: {
                        if isSaving {
                            ProgressView()
                        } else {
                            Text("Speichern")
                        }
                    }
                    .disabled(rawValue.trimmingCharacters(in: .whitespaces).isEmpty
                              || isSaving)
                }
            }
            // Bei jeder Eingabe-/Profilaenderung eine kleine Debounce-Preview.
            .onChange(of: rawValue) { _, _ in schedulePreview() }
            .onChange(of: selectedProfileId) { _, _ in schedulePreview() }
            // Firmen-Default einmalig beim Oeffnen uebernehmen. Der Picker
            // wird bei gesetztem Default durch die Info-Anzeige ersetzt,
            // aber selectedProfileId muss trotzdem gefuellt sein damit
            // Preview + Save den richtigen Wert mitsenden.
            .onAppear {
                if !defaultProfileId.isEmpty && selectedProfileId.isEmpty {
                    selectedProfileId = defaultProfileId
                }
            }
        }
    }

    // MARK: - Preview

    @State private var previewTask: Task<Void, Never>?

    private func schedulePreview() {
        previewTask?.cancel()
        let trimmed = rawValue.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else {
            preview = nil
            return
        }
        previewTask = Task {
            try? await Task.sleep(nanoseconds: 350_000_000) // 350 ms Debounce
            if Task.isCancelled { return }
            await loadPreview()
        }
    }

    private func loadPreview() async {
        guard let base = settings.serverBaseURL,
              let client = ApiClientFactory.make(baseURL: base.absoluteString,
                                                 token: settings.bearerToken) else {
            return
        }
        await MainActor.run { isPreviewing = true }
        do {
            let p = try await client.previewCard(
                rawValue: rawValue.trimmingCharacters(in: .whitespaces),
                profileId: selectedProfileId.isEmpty ? nil : selectedProfileId
            )
            await MainActor.run {
                self.preview = p
                self.isPreviewing = false
            }
        } catch {
            await MainActor.run { isPreviewing = false }
            // Preview-Fehler bewusst stumm — der Save-Call gibt das echte
            // Error-Feedback, wenn der Wert wirklich nicht transformierbar ist.
        }
    }

    // MARK: - NFC-Scan

    private func scanNFC() async {
        await MainActor.run {
            isScanning = true
            errorMessage = nil
        }
        do {
            let uid = try await NFCCardScanner.scan()
            await MainActor.run {
                self.rawValue = uid    // triggert .onChange → schedulePreview
                self.isScanning = false
            }
        } catch NFCCardScanner.NFCError.cancelled {
            // User-Abbruch — stille Rueckkehr, keine Fehlermeldung.
            await MainActor.run { self.isScanning = false }
        } catch {
            await MainActor.run {
                self.errorMessage = error.localizedDescription
                self.isScanning = false
            }
        }
    }

    // MARK: - Save

    private func save() async {
        guard let base = settings.serverBaseURL,
              let client = ApiClientFactory.make(baseURL: base.absoluteString,
                                                 token: settings.bearerToken) else {
            errorMessage = String(localized: "Kein Server konfiguriert.")
            return
        }
        await MainActor.run {
            isSaving = true
            errorMessage = nil
        }
        do {
            let card = try await client.createCard(
                rawValue: rawValue.trimmingCharacters(in: .whitespaces),
                profileId: selectedProfileId.isEmpty ? nil : selectedProfileId,
                notes: notes.isEmpty ? nil : notes
            )
            await MainActor.run {
                onCreated(card)
                isSaving = false
                dismiss()
            }
        } catch {
            await MainActor.run {
                errorMessage = error.localizedDescription
                isSaving = false
            }
        }
    }

    // MARK: - Profile Grouping

    /// Gruppiert Profile nach Vendor — im Picker erscheinen "YSoft",
    /// "Elatec", "Generic" etc. als Section-Header. Leerer Vendor landet
    /// unter "Generisch".
    private struct ProfileGroup {
        let key: String
        let profiles: [CardProfile]
    }

    private var groupedProfiles: [ProfileGroup] {
        let sorted = profiles.sorted { a, b in
            if a.vendor.lowercased() == b.vendor.lowercased() {
                return a.name.lowercased() < b.name.lowercased()
            }
            return a.vendor.lowercased() < b.vendor.lowercased()
        }
        var groups: [String: [CardProfile]] = [:]
        for p in sorted {
            let key = p.vendor.isEmpty ? "Generisch" : p.vendor
            groups[key, default: []].append(p)
        }
        return groups.keys.sorted().map { k in
            ProfileGroup(key: k, profiles: groups[k] ?? [])
        }
    }

    private func profileLabel(_ p: CardProfile) -> String {
        if !p.readerModel.isEmpty && p.readerModel.lowercased() != "any" {
            return "\(p.name) — \(p.readerModel)"
        }
        return p.name
    }

    @ViewBuilder
    private func monoRow(_ label: LocalizedStringKey, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.system(.footnote, design: .monospaced))
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}
