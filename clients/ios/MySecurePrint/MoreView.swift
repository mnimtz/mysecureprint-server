import SwiftUI

/// Eigener "Mehr"-Tab — erscheint wenn Management- und Karten-Tab gleichzeitig
/// sichtbar sind (6 Tabs würden über das iOS-Limit von 5 gehen).
///
/// Enthält NavigationLinks zu CardsContent und AccountContent — beide ohne
/// eigenen NavigationStack, damit dieser hier nicht verdoppelt wird.
struct MoreView: View {
    @EnvironmentObject private var settings: SettingsStore

    var body: some View {
        NavigationStack {
            List {
                if settings.delegateEnabled {
                    NavigationLink(destination: DelegateTeamsView()) {
                        Label(String(localized: "Delegate-Teams"), systemImage: "person.3.fill")
                    }
                }
                NavigationLink(destination: CardsContent()) {
                    Label(String(localized: "Karten"), systemImage: "creditcard.fill")
                }
                // v0.8.0 — iOS AirPrint-Profile (Feature ist opt-in serverseitig;
                // Menüpunkt zeigen wir immer — Create-Endpoint gibt 403 wenn aus)
                NavigationLink(destination: AirPrintProfilesView()) {
                    Label(String(localized: "airprint_view_title"), systemImage: "printer.filled.and.paper")
                }
                NavigationLink(destination: AccountContent()) {
                    Label(String(localized: "Konto"), systemImage: "person.crop.circle")
                }
            }
            .listStyle(.insetGrouped)
            .brandNavStyle(title: String(localized: "Mehr"))
        }
    }
}
