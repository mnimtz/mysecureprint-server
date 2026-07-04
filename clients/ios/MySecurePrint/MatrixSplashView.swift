import SwiftUI

// MARK: - Style

enum MatrixStyle {
    /// Erster Login: voller schwarzer Hintergrund, Mind.-4-Sek., App-Name + "Initialisiere…"
    case splash
    /// Sync-Overlay: halbtransparent über aktuellem Screen, "Laden…", kein Mindest-Timer
    case overlay
}

// MARK: - Datenmodell

private struct RainColumn {
    let x: CGFloat
    let speed: CGFloat
    let trailLength: Int
    let timeOffset: Double
    let sequence: [Int]
}

// MARK: - MatrixSplashView

struct MatrixSplashView: View {

    let style: MatrixStyle
    let onDismiss: () -> Void

    @EnvironmentObject private var cache: AppCache

    // ── Palette ─────────────────────────────────────────────────────────────
    private static let green       = Color(red: 0.00, green: 0.85, blue: 0.25)
    private static let greenBright = Color(red: 0.80, green: 1.00, blue: 0.85)

    // ── Symbol-Pool ──────────────────────────────────────────────────────────
    private static let symbolPool: [String] = [
        "printer.fill",
        "doc.fill",
        "doc.text.fill",
        "paperplane.fill",
        "tray.full.fill",
        "lock.fill",
        "cloud.fill",
        "checkmark.circle.fill",
        "envelope.fill",
        "qrcode",
        "person.fill",
        "arrow.down.doc.fill",
        "printer.dotmatrix.fill",
        "doc.badge.arrow.up",
        "rectangle.and.arrow.up.right.and.arrow.down.left",
    ]

    // ── Konfiguration je Style ───────────────────────────────────────────────
    private var bgOpacity:       Double { style == .splash ? 1.0  : 0.22 }
    private var symbolOpacity:   Double { style == .splash ? 1.0  : 0.55 }
    private var symbolSize:   CGFloat   { style == .splash ? 19   : 17   }
    private var minDuration:     Double { style == .splash ? 4.0  : 0.0  }
    private var showLabel:         Bool { style == .splash             }

    // ── State ────────────────────────────────────────────────────────────────
    @State private var startTime  = Date.now
    @State private var columns: [RainColumn] = []
    @State private var opacity: Double = 0        // startet bei 0 → fade-in
    @State private var dataReady  = false
    @State private var timerReady = false

    // MARK: Body

    var body: some View {
        ZStack(alignment: .bottom) {
            Color.black.opacity(bgOpacity).ignoresSafeArea()

            if !columns.isEmpty {
                TimelineView(.animation) { tl in
                    Canvas { ctx, size in
                        draw(ctx: ctx, size: size,
                             elapsed: tl.date.timeIntervalSince(startTime))
                    }
                }
                .ignoresSafeArea()
            }

            if showLabel {
                // Splash: App-Name + Statuszeile
                VStack(spacing: 6) {
                    Text("MySecurePrint")
                        .font(.system(size: 15, weight: .semibold, design: .monospaced))
                        .foregroundColor(Self.green)
                    Text(String(localized: "Initialisiere…"))
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundColor(Self.green.opacity(0.55))
                }
                .padding(.bottom, 54)
            } else {
                // Overlay: kompaktes "Laden…"-Label unten-mittig
                Text(String(localized: "Laden…"))
                    .font(.system(size: 12, weight: .medium, design: .monospaced))
                    .foregroundColor(Self.green.opacity(0.80))
                    .padding(.bottom, 54)
            }
        }
        .opacity(opacity)
        .onAppear {
            buildColumns()
            // Fade-in: splash langsamer, overlay schneller
            withAnimation(.easeIn(duration: style == .splash ? 0.3 : 0.15)) {
                opacity = 1
            }
        }
        // ── Splash: wartet auf isInitialLoad + Mindest-Timer ────────────────
        .onChange(of: cache.isInitialLoad) { _, loading in
            if !loading && style == .splash {
                dataReady = true
                checkFinish()
            }
        }
        .task(id: style) {
            if style == .splash {
                try? await Task.sleep(for: .seconds(minDuration))
                timerReady = true
                checkFinish()
            }
        }
        // ── Overlay: verschwindet sobald isSyncing false wird ───────────────
        .onChange(of: cache.isSyncing) { _, syncing in
            if !syncing && style == .overlay {
                fadeOut()
            }
        }
    }

    // MARK: Spalten aufbauen

    private func buildColumns() {
        startTime = Date.now
        let screenW  = UIScreen.main.bounds.width
        let colWidth = symbolSize * 1.75
        let count    = Int(screenW / colWidth)
        var rng = SystemRandomNumberGenerator()

        columns = (0..<count).map { i in
            let seqLen = 5 + Int.random(in: 0...6, using: &rng)
            let seq    = (0..<seqLen).map { _ in
                Int.random(in: 0..<Self.symbolPool.count, using: &rng)
            }
            return RainColumn(
                x:           CGFloat(i) * colWidth + colWidth / 2,
                speed:       CGFloat.random(in: 85...210, using: &rng),
                trailLength: Int.random(in: 7...13, using: &rng),
                timeOffset:  Double.random(in: 0...5, using: &rng),
                sequence:    seq
            )
        }
    }

    // MARK: Zeichnen

    private func draw(ctx: GraphicsContext, size: CGSize, elapsed: Double) {
        let spacing = symbolSize * 1.6

        for col in columns {
            let cycleH = size.height + spacing * CGFloat(col.trailLength)
            let rawY   = CGFloat(elapsed + col.timeOffset) * col.speed
            let headY  = rawY.truncatingRemainder(dividingBy: cycleH)
                         - spacing * CGFloat(col.trailLength - 1)

            for i in 0..<col.trailLength {
                let y = headY + CGFloat(i) * spacing
                guard y > -spacing, y < size.height else { continue }

                let trailAlpha = i == 0
                    ? 1.0
                    : max(0, 1.0 - Double(i) / Double(col.trailLength) * 1.2)
                let alpha  = trailAlpha * symbolOpacity
                let color  = i == 0 ? Self.greenBright : Self.green
                let name   = Self.symbolPool[col.sequence[i % col.sequence.count]]

                var c = ctx
                c.opacity = alpha
                c.draw(
                    Text(Image(systemName: name))
                        .foregroundColor(color)
                        .font(.system(size: symbolSize, weight: .medium)),
                    at: CGPoint(x: col.x, y: y + symbolSize / 2),
                    anchor: .center
                )
            }
        }
    }

    // MARK: Abschluss

    private func checkFinish() {
        guard style == .splash, dataReady, timerReady else { return }
        fadeOut()
    }

    private func fadeOut() {
        let duration = style == .splash ? 0.55 : 0.2
        withAnimation(.easeIn(duration: duration)) { opacity = 0 }
        DispatchQueue.main.asyncAfter(deadline: .now() + duration + 0.05) {
            onDismiss()
        }
    }
}
