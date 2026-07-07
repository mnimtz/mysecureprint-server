import SwiftUI

// MARK: - Brand Colors

enum MSP {

    // Core palette
    static let navy     = Color(brandHex: "#002854")
    static let navyDeep = Color(brandHex: "#00123B")
    static let cyan     = Color(brandHex: "#00A0FB")
    static let gold     = Color(brandHex: "#FFC600")
    static let green    = Color(brandHex: "#00EB86")

    // Semantic states (Tungsten brand book)
    static let danger   = Color(brandHex: "#DC2626")  // Error
    static let warning  = Color(brandHex: "#F59E0B")  // Warning / processing

    // Surfaces
    static let glass    = Color.white.opacity(0.10)
    static let glassBorder = Color.white.opacity(0.20)

    // Gradient
    static let navyGradient = LinearGradient(
        colors: [navyDeep, navy],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
    )
}

extension Color {
    init(brandHex hex: String) {
        var s = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        if s.count == 3 {
            s = s.flatMap { ["\($0)", "\($0)"] }.joined()
        }
        var n: UInt64 = 0
        Scanner(string: s).scanHexInt64(&n)
        self.init(
            .sRGB,
            red:     Double((n >> 16) & 0xFF) / 255,
            green:   Double((n >>  8) & 0xFF) / 255,
            blue:    Double( n        & 0xFF) / 255,
            opacity: 1
        )
    }
}

// MARK: - Button Styles

struct GoldButtonStyle: ButtonStyle {
    var isLoading: Bool = false
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 16, weight: .bold))
            .foregroundColor(MSP.navy)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 16)
            .background(
                MSP.gold.opacity(configuration.isPressed ? 0.85 : 1)
            )
            .cornerRadius(14)
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
            .animation(.spring(response: 0.2), value: configuration.isPressed)
    }
}

struct CyanButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 15, weight: .semibold))
            .foregroundColor(.white)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .background(
                RoundedRectangle(cornerRadius: 14)
                    .fill(MSP.glass)
                    .overlay(
                        RoundedRectangle(cornerRadius: 14)
                            .stroke(MSP.glassBorder, lineWidth: 1)
                    )
                    .opacity(configuration.isPressed ? 0.7 : 1)
            )
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
            .animation(.spring(response: 0.2), value: configuration.isPressed)
    }
}

// MARK: - Glass Text Field

struct BrandTextField: View {
    let label: String
    let icon: String
    @Binding var text: String
    var isSecure: Bool = false
    var keyboardType: UIKeyboardType = .default

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .foregroundColor(MSP.cyan)
                .frame(width: 20)
            if isSecure {
                SecureField(label, text: $text)
                    .foregroundColor(.white)
                    .tint(MSP.cyan)
            } else {
                TextField(label, text: $text)
                    .foregroundColor(.white)
                    .tint(MSP.cyan)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(keyboardType)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
        .background(MSP.glass)
        .cornerRadius(12)
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(MSP.glassBorder, lineWidth: 1)
        )
    }
}

// MARK: - Brand Header (Logo + App Name)

struct BrandHeader: View {
    var subtitle: String? = nil
    var body: some View {
        VStack(spacing: 12) {
            Image("AppLogo")
                .renderingMode(.original)
                .resizable()
                .scaledToFit()
                .frame(width: 90, height: 90)
            Text("MySecurePrint")
                .font(.system(size: 28, weight: .bold, design: .rounded))
                .foregroundColor(.white)
            if let sub = subtitle {
                Text(sub)
                    .font(.system(size: 14, weight: .regular))
                    .foregroundColor(.white.opacity(0.6))
                    .multilineTextAlignment(.center)
            }
        }
    }
}

// MARK: - Branded Navigation Title modifier

extension View {
    /// Overload for static/localizable string literals — the literal is looked
    /// up in xcstrings via LocalizedStringKey.
    func brandNavStyle(title: LocalizedStringKey) -> some View {
        self
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(MSP.navy, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
    }

    /// Overload for dynamic String values (e.g. printer.name, user.email)
    /// that should not be looked up in xcstrings.
    func brandNavStyle(title: String) -> some View {
        self
            .navigationTitle(title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(MSP.navy, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
    }
}

// MARK: - Card Section (for non-Form layouts)
struct CardSection<Content: View>: View {
    let title: String?
    @ViewBuilder let content: () -> Content
    init(_ title: String? = nil, @ViewBuilder content: @escaping () -> Content) {
        self.title = title; self.content = content
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            if let t = title {
                Text(t.uppercased())
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(Color(.secondaryLabel))
                    .tracking(0.5)
                    .padding(.horizontal, 4)
            }
            VStack(spacing: 0) { content() }
                .background(Color(.systemBackground))
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        }
    }
}

// MARK: - Card Row (row inside a CardSection, with optional divider)
struct CardFormRow<Content: View>: View {
    var divider: Bool = true
    @ViewBuilder let content: () -> Content
    var body: some View {
        VStack(spacing: 0) {
            content()
                .padding(.horizontal, 16)
                .padding(.vertical, 13)
            if divider { Divider().padding(.leading, 16) }
        }
    }
}
