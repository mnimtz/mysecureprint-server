package email.nimtz.mysecureprint.ui

import android.net.Uri
import androidx.compose.runtime.*
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import email.nimtz.mysecureprint.data.store.SettingsStore
import email.nimtz.mysecureprint.ui.screens.*

sealed class Screen(val route: String) {
    object Setup  : Screen("setup")
    object Login  : Screen("login")
    object Main   : Screen("main")
}

@Composable
fun NavGraph(settings: SettingsStore, initialSharedFileUri: Uri?) {
    val nav = rememberNavController()
    val startDest = if (settings.isLoggedIn) Screen.Main.route else Screen.Setup.route

    NavHost(navController = nav, startDestination = startDest) {
        composable(Screen.Setup.route) {
            SetupScreen(settings = settings, onServerReady = {
                nav.navigate(Screen.Login.route) {
                    popUpTo(Screen.Setup.route) { inclusive = false }
                }
            })
        }
        composable(Screen.Login.route) {
            LoginScreen(settings = settings, onLoginSuccess = {
                nav.navigate(Screen.Main.route) {
                    popUpTo(Screen.Setup.route) { inclusive = true }
                }
            })
        }
        composable(Screen.Main.route) {
            MainScreen(
                settings = settings,
                initialSharedFileUri = initialSharedFileUri,
                onLogout = {
                    nav.navigate(Screen.Setup.route) {
                        popUpTo(Screen.Main.route) { inclusive = true }
                    }
                },
            )
        }
    }
}
