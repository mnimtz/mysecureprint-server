import SwiftUI
import PrintixSendCore

// MARK: - Drucker-Liste

struct PrinterListView: View {
    let printers: [MgmtPrinter]
    @State private var query = ""
    @State private var onlineFilter: Bool? = nil

    private var filtered: [MgmtPrinter] {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return printers.filter { p in
            if let f = onlineFilter, (p.isOnline == true) != f { return false }
            guard !q.isEmpty else { return true }
            return p.name.lowercased().contains(q)
                || (p.location ?? "").lowercased().contains(q)
                || (p.model ?? "").lowercased().contains(q)
                || (p.status ?? "").lowercased().contains(q)
                || (p.queueId ?? "").lowercased().contains(q)
        }
    }

    var body: some View {
        List {
            Section {
                HStack(spacing: 8) {
                    mgmtFilterChip(String(localized: "Alle"),    selected: onlineFilter == nil)   { onlineFilter = nil }
                    mgmtFilterChip(String(localized: "Online"),  selected: onlineFilter == true)  { onlineFilter = true }
                    mgmtFilterChip(String(localized: "Offline"), selected: onlineFilter == false) { onlineFilter = false }
                    Spacer()
                }
            }
            .listRowInsets(EdgeInsets(top: 6, leading: 12, bottom: 6, trailing: 12))
            .listRowBackground(Color.clear)
            .listRowSeparator(.hidden)

            Section {
                if filtered.isEmpty {
                    Text(query.isEmpty ? String(localized: "Keine Drucker.") : String(localized: "Keine Treffer."))
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    ForEach(filtered) { p in
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
                }
            }
        }
        .listStyle(.insetGrouped)
        .searchable(text: $query,
            placement: .navigationBarDrawer(displayMode: .always),
            prompt: String(localized: "Drucker suchen"))
        .brandNavStyle(title: String(localized: "Drucker"))
        .tint(MSP.cyan)
    }
}

// MARK: - Benutzer-Liste

struct UserListView: View {
    let users: [MgmtUser]
    @State private var query = ""
    @State private var roleFilter: String? = nil

    private func roleMatches(_ role: String?, filter: String) -> Bool {
        let r = (role ?? "").uppercased()
        switch filter {
        case "employee": return !r.contains("GUEST")
        case "guest":    return r.contains("GUEST")
        default:         return true
        }
    }

    private var filtered: [MgmtUser] {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return users.filter { u in
            if let rf = roleFilter {
                guard roleMatches(u.role, filter: rf) else { return false }
            }
            guard !q.isEmpty else { return true }
            return (u.name ?? "").lowercased().contains(q)
                || (u.email ?? "").lowercased().contains(q)
        }
    }

    var body: some View {
        List {
            Section {
                HStack(spacing: 8) {
                    mgmtFilterChip(String(localized: "Alle"),        selected: roleFilter == nil)          { roleFilter = nil }
                    mgmtFilterChip(String(localized: "Mitarbeiter"), selected: roleFilter == "employee") { roleFilter = "employee" }
                    mgmtFilterChip(String(localized: "Gäste"),       selected: roleFilter == "guest")    { roleFilter = "guest" }
                    Spacer()
                }
            }
            .listRowInsets(EdgeInsets(top: 6, leading: 12, bottom: 6, trailing: 12))
            .listRowBackground(Color.clear)
            .listRowSeparator(.hidden)

            Section {
                if filtered.isEmpty {
                    Text(query.isEmpty ? String(localized: "Keine Benutzer.") : String(localized: "Keine Treffer."))
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    ForEach(filtered) { u in
                        NavigationLink(value: u) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(u.name ?? u.email ?? u.id).font(.body)
                                if let e = u.email, !e.isEmpty, e != u.name {
                                    Text(e).font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .searchable(text: $query,
            placement: .navigationBarDrawer(displayMode: .always),
            prompt: String(localized: "Benutzer suchen"))
        .brandNavStyle(title: String(localized: "Benutzer"))
        .tint(MSP.cyan)
    }
}

// MARK: - Arbeitsplatz-Liste

struct WorkstationListView: View {
    let workstations: [MgmtWorkstation]
    @State private var query = ""
    @State private var onlineFilter: Bool? = nil

    private var filtered: [MgmtWorkstation] {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return workstations.filter { w in
            if let f = onlineFilter, (w.isOnline == true) != f { return false }
            guard !q.isEmpty else { return true }
            return w.hostname.lowercased().contains(q)
                || (w.userEmail ?? "").lowercased().contains(q)
                || (w.description ?? "").lowercased().contains(q)
        }
    }

    var body: some View {
        List {
            Section {
                HStack(spacing: 8) {
                    mgmtFilterChip(String(localized: "Alle"),    selected: onlineFilter == nil)   { onlineFilter = nil }
                    mgmtFilterChip(String(localized: "Online"),  selected: onlineFilter == true)  { onlineFilter = true }
                    mgmtFilterChip(String(localized: "Offline"), selected: onlineFilter == false) { onlineFilter = false }
                    Spacer()
                }
            }
            .listRowInsets(EdgeInsets(top: 6, leading: 12, bottom: 6, trailing: 12))
            .listRowBackground(Color.clear)
            .listRowSeparator(.hidden)

            Section {
                if filtered.isEmpty {
                    Text(query.isEmpty ? String(localized: "Keine Arbeitsplätze.") : String(localized: "Keine Treffer."))
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    ForEach(filtered) { w in
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
                }
            }
        }
        .listStyle(.insetGrouped)
        .searchable(text: $query,
            placement: .navigationBarDrawer(displayMode: .always),
            prompt: String(localized: "Arbeitsplatz suchen"))
        .brandNavStyle(title: String(localized: "Arbeitsplätze"))
        .tint(MSP.cyan)
    }
}

// MARK: - Drucker-Detail

struct PrinterDetailView: View {
    let printer: MgmtPrinter
    @EnvironmentObject private var settings: SettingsStore

    @State private var detail: MgmtPrinterDetail? = nil
    @State private var isLoadingDetail = false

    var body: some View {
        List {
            // ── Header ──────────────────────────────────────────────────────
            Section {
                HStack(spacing: 16) {
                    ZStack {
                        RoundedRectangle(cornerRadius: 12)
                            .fill(MSP.navyGradient)
                            .frame(width: 56, height: 56)
                        Image(systemName: "printer.fill")
                            .font(.title2)
                            .foregroundColor(.white)
                    }
                    VStack(alignment: .leading, spacing: 5) {
                        Text(printer.name).font(.headline)
                        onlineChip(online: printer.isOnline == true)
                    }
                }
                .padding(.vertical, 4)
            }

            // ── Toner / Verbrauchsmaterialien ────────────────────────────────
            tonerSection

            // ── Informationen ────────────────────────────────────────────────
            Section(String(localized: "Informationen")) {
                if let model = printer.model, !model.isEmpty {
                    infoRow(icon: "cpu", label: String(localized: "Modell"), value: model)
                }
                if let vendor = detail?.printer?.vendor, !vendor.isEmpty {
                    infoRow(icon: "building", label: String(localized: "Hersteller"), value: vendor)
                }
                if let serial = detail?.printer?.serialNo, !serial.isEmpty {
                    infoRow(icon: "barcode", label: String(localized: "Seriennummer"), value: serial)
                }
                if let loc = printer.location, !loc.isEmpty {
                    infoRow(icon: "mappin", label: String(localized: "Standort"), value: loc)
                }
                if let qid = printer.queueId, !qid.isEmpty {
                    infoRow(icon: "tray.full", label: String(localized: "Queue-ID"), value: qid)
                }
                if let s = printer.status, !s.isEmpty {
                    infoRow(icon: "circle.fill",
                            label: String(localized: "Status"),
                            value: s,
                            valueColor: printer.isOnline == true ? .green : .secondary)
                }
                if let caps = detail?.printer?.capabilities {
                    infoRow(icon: caps.color == true ? "paintpalette.fill" : "circle.fill",
                            label: String(localized: "Farbdruck"),
                            value: caps.color == true
                                ? String(localized: "Ja")
                                : String(localized: "Nein"))
                }
            }
        }
        .listStyle(.insetGrouped)
        .brandNavStyle(title: printer.name)
        .tint(MSP.cyan)
        .task { await loadDetail() }
    }

    // MARK: - Toner Section

    @ViewBuilder
    private var tonerSection: some View {
        let supplies = detail?.printer?.supplies ?? []
        let isColor  = detail?.printer?.capabilities?.color

        Section {
            if !supplies.isEmpty {
                // Echtdaten von Printix
                ForEach(Array(supplies.enumerated()), id: \.offset) { _, supply in
                    TonerBar(
                        label: tonerLabel(supply.color),
                        color: tonerColor(supply.color),
                        percent: supply.percent
                    )
                }
            } else if isLoadingDetail {
                HStack { Spacer(); ProgressView().controlSize(.small); Spacer() }
            } else {
                // Printix liefert keine Toner-Level — hübsche Platzhalter zeigen
                let channels: [(String, Color)] = isColor == false
                    ? [("K", .init(white: 0.2))]
                    : [("K", .init(white: 0.2)),
                       ("C", Color(red: 0.0, green: 0.7, blue: 0.9)),
                       ("M", Color(red: 0.85, green: 0.15, blue: 0.5)),
                       ("Y", Color(red: 0.95, green: 0.82, blue: 0.1))]
                ForEach(channels, id: \.0) { label, color in
                    TonerBar(label: label, color: color, percent: nil)
                }
            }
        } header: {
            Text(String(localized: "Verbrauchsmaterialien"))
        } footer: {
            if detail != nil && (detail?.printer?.supplies ?? []).isEmpty {
                Text(String(localized: "Toner-Level werden von Printix nicht bereitgestellt."))
            }
        }
    }

    // MARK: - Detail Fetch

    private func loadDetail() async {
        guard let base = settings.serverBaseURL,
              let client = ApiClientFactory.make(baseURL: base.absoluteString,
                                                 token: settings.bearerToken) else { return }
        isLoadingDetail = true
        defer { isLoadingDetail = false }
        detail = try? await client.managementPrinterDetail(
            printerId: printer.id,
            queueId: printer.queueId ?? ""
        )
    }

    // MARK: - Helpers

    private func tonerLabel(_ color: String?) -> String {
        switch color?.lowercased() {
        case "cyan":    return "C"
        case "magenta": return "M"
        case "yellow":  return "Y"
        default:        return "K"
        }
    }

    private func tonerColor(_ color: String?) -> Color {
        switch color?.lowercased() {
        case "cyan":    return Color(red: 0.0, green: 0.7, blue: 0.9)
        case "magenta": return Color(red: 0.85, green: 0.15, blue: 0.5)
        case "yellow":  return Color(red: 0.95, green: 0.82, blue: 0.1)
        default:        return Color(white: 0.2)
        }
    }
}

// MARK: - Benutzer-Detail

struct UserDetailView: View {
    let user: MgmtUser
    @EnvironmentObject private var settings: SettingsStore

    @State private var detail: MgmtUserDetail? = nil
    @State private var isLoadingDetail = false

    private var effectiveRole: String? {
        if let r = detail?.role, !r.isEmpty { return r }
        return user.role.flatMap { $0.isEmpty ? nil : $0 }
    }

    private var initials: String {
        let n = user.name ?? user.email ?? "?"
        return n.split(separator: " ")
            .prefix(2)
            .compactMap { $0.first.map(String.init) }
            .joined()
            .uppercased()
    }

    var body: some View {
        List {
            Section {
                HStack(spacing: 16) {
                    ZStack {
                        Circle()
                            .fill(MSP.navyGradient)
                            .frame(width: 56, height: 56)
                        Text(initials.isEmpty ? "?" : initials)
                            .font(.system(size: 20, weight: .bold, design: .rounded))
                            .foregroundColor(.white)
                    }
                    VStack(alignment: .leading, spacing: 5) {
                        if let n = user.name, !n.isEmpty {
                            Text(n).font(.headline)
                        }
                        if let e = user.email, !e.isEmpty {
                            Text(e)
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                        }
                        if let role = effectiveRole {
                            roleChip(role: role)
                        }
                    }
                }
                .padding(.vertical, 4)
            }

            Section(String(localized: "Konto")) {
                if let e = user.email, !e.isEmpty {
                    infoRow(icon: "envelope", label: String(localized: "E-Mail"), value: e)
                }
                if let role = effectiveRole {
                    infoRow(icon: "person.badge.key", label: String(localized: "Rolle"), value: role.capitalized)
                }
                if let code = detail?.idCode, !code.isEmpty {
                    infoRow(icon: "number", label: String(localized: "ID-Code"), value: code)
                }
                if let pin = detail?.pin, !pin.isEmpty {
                    infoRow(icon: "lock.fill", label: String(localized: "PIN"), value: pin)
                }
                if let lang = detail?.language, !lang.isEmpty {
                    infoRow(icon: "globe", label: String(localized: "Sprache"), value: lang.uppercased())
                }
                if let methods = detail?.authMethods, !methods.isEmpty {
                    infoRow(icon: "key.fill",
                            label: String(localized: "Anmeldung"),
                            value: methods.map { $0.capitalized }.joined(separator: ", "))
                }
                if let exp = detail?.expiry, !exp.isEmpty {
                    infoRow(icon: "clock.badge.exclamationmark",
                            label: String(localized: "Ablaufdatum"),
                            value: formatTimestamp(exp))
                }
                if let created = detail?.created, !created.isEmpty {
                    infoRow(icon: "calendar",
                            label: String(localized: "Erstellt"),
                            value: formatTimestamp(created))
                }
                if let modified = detail?.modified, !modified.isEmpty {
                    infoRow(icon: "pencil.circle",
                            label: String(localized: "Geändert"),
                            value: formatTimestamp(modified))
                }
            }

            if let roles = detail?.roles, !roles.isEmpty {
                Section(String(localized: "Rollen")) {
                    ForEach(roles, id: \.self) { r in
                        HStack {
                            Image(systemName: "checkmark.circle.fill")
                                .foregroundStyle(MSP.cyan)
                                .frame(width: 26)
                            Text(r.capitalized)
                        }
                    }
                }
            }

            if let groups = detail?.groups, !groups.isEmpty {
                Section(String(localized: "Gruppen")) {
                    ForEach(groups, id: \.self) { g in
                        HStack {
                            Image(systemName: "person.2.fill")
                                .foregroundStyle(MSP.cyan)
                                .frame(width: 26)
                            Text(g)
                                .font(.subheadline)
                        }
                    }
                }
            }

            if let cards = detail?.cards, !cards.isEmpty {
                Section(String(localized: "RFID-Karten")) {
                    ForEach(cards, id: \.id) { card in
                        HStack {
                            Image(systemName: "creditcard.fill")
                                .foregroundStyle(MSP.cyan)
                                .frame(width: 26)
                            VStack(alignment: .leading, spacing: 2) {
                                if let num = card.number, !num.isEmpty {
                                    Text(num).font(.subheadline.monospacedDigit())
                                } else if !card.id.isEmpty {
                                    // UUID-Suffix als lesbarer Fallback
                                    Text("…" + card.id.suffix(8).uppercased())
                                        .font(.subheadline.monospacedDigit())
                                        .foregroundStyle(.secondary)
                                }
                                if let t = card.cardType, !t.isEmpty {
                                    Text(t).font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                }
            }

            if isLoadingDetail {
                Section {
                    HStack { Spacer(); ProgressView().controlSize(.small); Spacer() }
                }
            }
        }
        .listStyle(.insetGrouped)
        .brandNavStyle(title: user.name ?? user.email ?? user.id)
        .tint(MSP.cyan)
        .task { await loadDetail() }
    }

    private func loadDetail() async {
        guard let base = settings.serverBaseURL,
              let client = ApiClientFactory.make(baseURL: base.absoluteString,
                                                 token: settings.bearerToken) else { return }
        isLoadingDetail = true
        defer { isLoadingDetail = false }
        detail = try? await client.managementUserDetail(userId: user.id)
    }

    private func roleChip(role: String) -> some View {
        let isAdmin = role.lowercased().contains("admin")
        return Text(role.capitalized)
            .font(.caption2.bold())
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(isAdmin ? MSP.cyan.opacity(0.18) : Color.gray.opacity(0.15))
            .foregroundColor(isAdmin ? MSP.cyan : .secondary)
            .clipShape(Capsule())
    }
}

// MARK: - Workstation-Detail

struct WorkstationDetailView: View {
    let workstation: MgmtWorkstation
    @EnvironmentObject private var settings: SettingsStore
    @EnvironmentObject private var cache: AppCache

    @State private var detail: MgmtWorkstationDetail? = nil
    @State private var isLoadingDetail = false

    private var linkedUser: MgmtUser? {
        let email = detail?.userEmail ?? workstation.userEmail ?? ""
        guard !email.isEmpty else { return nil }
        return cache.mgmtUsers.first { ($0.email ?? "").lowercased() == email.lowercased() }
    }

    var body: some View {
        List {
            Section {
                HStack(spacing: 16) {
                    ZStack {
                        RoundedRectangle(cornerRadius: 12)
                            .fill(MSP.navyGradient)
                            .frame(width: 56, height: 56)
                        Image(systemName: "desktopcomputer")
                            .font(.title2)
                            .foregroundColor(.white)
                    }
                    VStack(alignment: .leading, spacing: 5) {
                        Text(workstation.hostname)
                            .font(.headline)
                        let online = detail?.isOnline ?? (workstation.isOnline == true)
                        onlineChip(online: online)
                    }
                }
                .padding(.vertical, 4)
            }

            Section(String(localized: "Informationen")) {
                let email = detail?.userEmail ?? workstation.userEmail
                if let e = email, !e.isEmpty {
                    if let u = linkedUser {
                        NavigationLink(value: u) {
                            HStack {
                                Image(systemName: "person.fill")
                                    .foregroundStyle(Color.accentColor)
                                    .frame(width: 26)
                                VStack(alignment: .leading, spacing: 2) {
                                    if let name = u.name, !name.isEmpty, name != e {
                                        Text(name).font(.subheadline)
                                        Text(e).font(.caption).foregroundStyle(.secondary)
                                    } else {
                                        Text(e).font(.subheadline)
                                    }
                                }
                            }
                        }
                    } else {
                        infoRow(icon: "person", label: String(localized: "Benutzer"), value: e)
                    }
                }
                // v0.7.224 — Fallback auf base workstation, damit Info bereits VOR
                // Detail-Fetch sichtbar ist (Server sendet description + siteId schon
                // in der Liste — vorher unterschlagen weil im Model gefehlt).
                let desc = detail?.description ?? workstation.description
                if let d = desc, !d.isEmpty {
                    infoRow(icon: "text.alignleft", label: String(localized: "Beschreibung"), value: d)
                }
                let sid = detail?.siteId ?? workstation.siteId
                if let s = sid, !s.isEmpty {
                    infoRow(icon: "building.2", label: String(localized: "Site-ID"), value: s)
                }
                let lastSeen = detail?.lastSeen ?? workstation.lastSeen
                if let ls = lastSeen, !ls.isEmpty {
                    infoRow(icon: "clock", label: String(localized: "Zuletzt aktiv"),
                            value: formatTimestamp(ls))
                }
                if let lc = detail?.lastConnectTime, !lc.isEmpty {
                    infoRow(icon: "network", label: String(localized: "Verbunden"),
                            value: formatTimestamp(lc))
                }
                if let ld = detail?.lastDisconnectTime, !ld.isEmpty {
                    infoRow(icon: "network.slash", label: String(localized: "Getrennt"),
                            value: formatTimestamp(ld))
                }
            }

            if let netIds = detail?.networkIds, !netIds.isEmpty {
                Section(String(localized: "Netzwerke")) {
                    ForEach(netIds, id: \.self) { nid in
                        HStack {
                            Image(systemName: "wifi")
                                .foregroundStyle(MSP.cyan)
                                .frame(width: 26)
                            Text(nid).font(.subheadline)
                        }
                    }
                }
            }

            if isLoadingDetail {
                Section {
                    HStack { Spacer(); ProgressView().controlSize(.small); Spacer() }
                }
            }
        }
        .listStyle(.insetGrouped)
        .brandNavStyle(title: workstation.hostname)
        .tint(MSP.cyan)
        .task { await loadDetail() }
    }

    private func loadDetail() async {
        guard let base = settings.serverBaseURL,
              let client = ApiClientFactory.make(baseURL: base.absoluteString,
                                                 token: settings.bearerToken) else { return }
        isLoadingDetail = true
        defer { isLoadingDetail = false }
        detail = try? await client.managementWorkstationDetail(workstationId: workstation.id)
    }
}

// MARK: - Toner Bar Component

private struct TonerBar: View {
    let label: String
    let color: Color
    let percent: Double?   // nil = no data

    private var fillColor: Color {
        guard let pct = percent else { return color.opacity(0.35) }
        if pct < 10 { return .red }
        if pct < 20 { return .orange }
        return color
    }

    var body: some View {
        HStack(spacing: 10) {
            // Kanal-Kürzel (K / C / M / Y)
            ZStack {
                Circle()
                    .fill(color.opacity(0.18))
                    .frame(width: 28, height: 28)
                Text(label)
                    .font(.system(size: 12, weight: .bold, design: .rounded))
                    .foregroundStyle(color)
            }

            // Fortschrittsbalken
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    // Hintergrund-Track
                    RoundedRectangle(cornerRadius: 4)
                        .fill(Color(.systemFill))
                        .frame(height: 10)
                    // Füllstand
                    if let pct = percent {
                        RoundedRectangle(cornerRadius: 4)
                            .fill(fillColor)
                            .frame(width: max(8, geo.size.width * pct / 100), height: 10)
                            .animation(.easeOut(duration: 0.5), value: pct)
                    }
                }
                .frame(maxWidth: .infinity)
            }
            .frame(height: 10)

            // Prozentangabe
            Text(percent.map { "\(Int($0.rounded()))%" } ?? "–")
                .font(.system(size: 13, weight: .medium).monospacedDigit())
                .foregroundStyle(percent == nil ? Color.secondary : fillColor)
                .frame(width: 38, alignment: .trailing)
        }
        .padding(.vertical, 3)
    }
}

// MARK: - Shared Helpers

private func formatTimestamp(_ raw: String) -> String {
    // ISO 8601 variants
    let formats = [
        "yyyy-MM-dd'T'HH:mm:ssZ",
        "yyyy-MM-dd'T'HH:mm:ss.SSSZ",
        "yyyy-MM-dd'T'HH:mm:ssXXXXX",
        "yyyy-MM-dd HH:mm:ss",
    ]
    let df = DateFormatter()
    for fmt in formats {
        df.dateFormat = fmt
        if let d = df.date(from: raw) {
            let out = DateFormatter()
            out.dateStyle = .short
            out.timeStyle = .short
            return out.string(from: d)
        }
    }
    // Unix epoch (milliseconds)
    if let ms = Double(raw) {
        let d = Date(timeIntervalSince1970: ms / 1000)
        let out = DateFormatter()
        out.dateStyle = .short
        out.timeStyle = .short
        return out.string(from: d)
    }
    return raw
}

private func onlineChip(online: Bool) -> some View {
    Text(online ? String(localized: "Online") : String(localized: "Offline"))
        .font(.caption2.bold())
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .background(online ? Color.green.opacity(0.15) : Color.gray.opacity(0.15))
        .foregroundColor(online ? .green : .secondary)
        .clipShape(Capsule())
}

private func infoRow(icon: String, label: String, value: String,
                     valueColor: Color = .secondary) -> some View {
    HStack {
        Image(systemName: icon)
            .foregroundStyle(Color.accentColor)
            .frame(width: 26)
        Text(label)
        Spacer()
        Text(value)
            .foregroundStyle(valueColor)
            .font(.subheadline)
            .multilineTextAlignment(.trailing)
            .lineLimit(2)
    }
}

private func mgmtFilterChip(_ label: String, selected: Bool, action: @escaping () -> Void) -> some View {
    Button(action: action) {
        Text(label)
            .font(.caption)
            .fontWeight(.medium)
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(selected ? MSP.cyan : Color(.tertiarySystemFill))
            .foregroundColor(selected ? .white : .secondary)
            .clipShape(Capsule())
    }
    .buttonStyle(.plain)
}
