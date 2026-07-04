package email.nimtz.mysecureprint.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// Brand colours matching iOS MSP.* values
object MSPColors {
    val Cyan       = Color(0xFF00B8D4)
    val CyanDark   = Color(0xFF0097A7)
    val Navy       = Color(0xFF1A2344)
    val NavyLight  = Color(0xFF2D3A5C)
    val Teal       = Color(0xFF00ACC1)
    val Background = Color(0xFF0D1117)
    val Surface    = Color(0xFF161B22)
    val Card       = Color(0xFF1C2128)
    val Border     = Color(0xFF30363D)
    val TextPrimary   = Color(0xFFE6EDF3)
    val TextSecondary = Color(0xFF8B949E)
    val Success    = Color(0xFF3FB950)
    val Warning    = Color(0xFFD29922)
    val Error      = Color(0xFFF85149)
}

private val DarkColorScheme = darkColorScheme(
    primary          = MSPColors.Cyan,
    onPrimary        = Color.Black,
    primaryContainer = MSPColors.NavyLight,
    secondary        = MSPColors.Teal,
    background       = MSPColors.Background,
    surface          = MSPColors.Surface,
    surfaceVariant   = MSPColors.Card,
    onBackground     = MSPColors.TextPrimary,
    onSurface        = MSPColors.TextPrimary,
    onSurfaceVariant = MSPColors.TextSecondary,
    outline          = MSPColors.Border,
    error            = MSPColors.Error,
)

private val LightColorScheme = lightColorScheme(
    primary          = MSPColors.CyanDark,
    onPrimary        = Color.White,
    primaryContainer = Color(0xFFE0F7FA),
    secondary        = MSPColors.Teal,
    background       = Color(0xFFF5F5F5),
    surface          = Color.White,
    surfaceVariant   = Color(0xFFF0F0F0),
    onBackground     = Color(0xFF1A1A1A),
    onSurface        = Color(0xFF1A1A1A),
    onSurfaceVariant = Color(0xFF666666),
    outline          = Color(0xFFDDDDDD),
    error            = MSPColors.Error,
)

@Composable
fun MSPTheme(darkTheme: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    val colors = if (darkTheme) DarkColorScheme else LightColorScheme
    MaterialTheme(colorScheme = colors, typography = Typography(), content = content)
}
