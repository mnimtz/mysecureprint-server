import SwiftUI

// MARK: - Datenmodell

private struct RainColumn {
    let x: CGFloat
    let speed: CGFloat       // Pixel pro Sekunde
    let trailLength: Int     // Anzahl Symbole im Schweif
    let timeOffset: Double   // Versatz damit Spalten versetzt starten
    let sequence: [Int]      // zyklische Indizes in symbolPool
}

// MARK: - Hauptview

/// Matrix-Regen mit Drucker- und Dokument-Symbolen.
/// Läuft so lange bis BEIDE Bedingungen erfüllt sind:
///   1. cache.isInitialLoad == false  (Daten vollständig geladen)
///   2. minDuration (4 s) abgelaufen
/// Danach Fade-out → onDismiss.
struct MatrixSplashView: View {

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

    private let symbolSize: CGFloat = 19
    private let minDuration: Double = 4.0

    // ── State ────────────────────────────────────────────────────────────────
    @State private var startTime   = Date.now
    @State private var columns: [RainColumn] = []
    @State private var opacity: Double = 1.0
    @State private var dataReady   = false
    @State private var timerReady  = false

    // MARK: Body

    var body: some View {
        ZStack(alignment: .bottom) {
            Color.black.ignoresSafeArea()

            if !columns.isEmpty {
                TimelineView(.animation) { tl in
                    Canvas { ctx, size in
                        draw(ctx: ctx, size: size,
                             elapsed: tl.date.timeIntervalSince(startTime))
                    }
                }
                .ignoresSafeArea()
            }

            VStack(spacing: 6) {
                Text("MySecurePrint")
                    .font(.system(size: 15, weight: .semibold, design: .monospaced))
                    .foregroundColor(Self.green)
                Text(String(localized: "Initialisiere…"))
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundColor(Self.green.opacity(0.55))
            }
            .padding(.bottom, 54)
        }
        .opacity(opacity)
        .onAppear { buildColumns() }
        .onChange(of: cache.isInitialLoad) { _, loading in
            if !loading { dataReady = true; checkFinish() }
        }
        .task {
            try? await Task.sleep(for: .seconds(minDuration))
            timerReady = true
            checkFinish()
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

                let alpha  = i == 0
                    ? 1.0
                    : max(0, 1.0 - Double(i) / Double(col.trailLength) * 1.2)
                let color  = i == 0 ? Self.greenBright : Self.green
                let symIdx = col.sequence[i % col.sequence.count]
                let name   = Self.symbolPool[symIdx]

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
        guard dataReady && timerReady else { return }
        withAnimation(.easeIn(duration: 0.55)) { opacity = 0 }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { onDismiss() }
    }
}
