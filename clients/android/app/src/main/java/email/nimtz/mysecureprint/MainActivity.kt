package email.nimtz.mysecureprint

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import email.nimtz.mysecureprint.data.store.SettingsStore
import email.nimtz.mysecureprint.ui.MSPTheme
import email.nimtz.mysecureprint.ui.NavGraph

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        installSplashScreen()
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        val settings = SettingsStore(applicationContext)
        val sharedFileUri = if (intent?.action == Intent.ACTION_SEND)
            intent.getParcelableExtra<android.net.Uri>(Intent.EXTRA_STREAM) else null

        setContent {
            MSPTheme {
                NavGraph(
                    settings = settings,
                    initialSharedFileUri = sharedFileUri,
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
    }
}
