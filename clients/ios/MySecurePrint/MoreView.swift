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
                NavigationLink(destination: CardsContent()) {
                    Label(String(localized: "Karten"), systemImage: "creditcard.fill")
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
