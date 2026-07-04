package email.nimtz.mysecureprint.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel
import email.nimtz.mysecureprint.data.store.SettingsStore
import email.nimtz.mysecureprint.ui.MSPColors
import email.nimtz.mysecureprint.ui.viewmodels.LoginViewModel
import email.nimtz.mysecureprint.ui.viewmodels.LoginViewModelFactory
import email.nimtz.mysecureprint.ui.viewmodels.LoginUiState

@Composable
fun LoginScreen(settings: SettingsStore, onLoginSuccess: () -> Unit) {
    val context = LocalContext.current
    val vm: LoginViewModel = viewModel(factory = LoginViewModelFactory(settings))
    val uiState by vm.uiState.collectAsState()

    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var showPassword by remember { mutableStateOf(false) }

    LaunchedEffect(uiState) {
        if (uiState is LoginUiState.Success) onLoginSuccess()
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Brush.verticalGradient(listOf(MSPColors.Navy, MSPColors.NavyLight)))
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 28.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Spacer(Modifier.height(72.dp))

            Icon(Icons.Default.Print, null, tint = MSPColors.Cyan, modifier = Modifier.size(52.dp))
            Spacer(Modifier.height(12.dp))
            Text("MySecurePrint", color = MaterialTheme.colorScheme.onBackground,
                fontSize = 26.sp, fontWeight = FontWeight.Bold)
            Text("Sicher drucken. Überall.", color = MaterialTheme.colorScheme.onSurfaceVariant,
                fontSize = 14.sp)

            Spacer(Modifier.height(40.dp))

            // Username
            OutlinedTextField(
                value = username,
                onValueChange = { username = it; vm.clearError() },
                label = { Text("E-Mail / Benutzername") },
                leadingIcon = { Icon(Icons.Default.Person, null) },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email),
                modifier = Modifier.fillMaxWidth(),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = MSPColors.Cyan, focusedLabelColor = MSPColors.Cyan),
            )

            Spacer(Modifier.height(12.dp))

            // Password
            OutlinedTextField(
                value = password,
                onValueChange = { password = it; vm.clearError() },
                label = { Text("Passwort") },
                leadingIcon = { Icon(Icons.Default.Lock, null) },
                trailingIcon = {
                    IconButton(onClick = { showPassword = !showPassword }) {
                        Icon(
                            if (showPassword) Icons.Default.VisibilityOff else Icons.Default.Visibility,
                            contentDescription = null, tint = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                },
                singleLine = true,
                visualTransformation = if (showPassword) VisualTransformation.None else PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                modifier = Modifier.fillMaxWidth(),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = MSPColors.Cyan, focusedLabelColor = MSPColors.Cyan),
            )

            Spacer(Modifier.height(20.dp))

            // Login button
            Button(
                onClick = { vm.loginWithPassword(username.trim(), password) },
                enabled = uiState !is LoginUiState.Loading && username.isNotBlank() && password.isNotBlank(),
                modifier = Modifier.fillMaxWidth().height(52.dp),
                colors = ButtonDefaults.buttonColors(containerColor = MSPColors.Cyan),
                shape = RoundedCornerShape(12.dp),
            ) {
                if (uiState is LoginUiState.Loading && (uiState as? LoginUiState.Loading)?.source == "password") {
                    CircularProgressIndicator(modifier = Modifier.size(20.dp), color = MSPColors.Navy, strokeWidth = 2.dp)
                } else {
                    Icon(Icons.Default.ArrowForward, null)
                    Spacer(Modifier.width(8.dp))
                    Text("Einloggen", fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
                }
            }

            Spacer(Modifier.height(20.dp))

            // Divider
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
                HorizontalDivider(modifier = Modifier.weight(1f), color = MaterialTheme.colorScheme.outline.copy(alpha = 0.4f))
                Text("  oder  ", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 12.sp)
                HorizontalDivider(modifier = Modifier.weight(1f), color = MaterialTheme.colorScheme.outline.copy(alpha = 0.4f))
            }

            Spacer(Modifier.height(20.dp))

            // Microsoft login
            OutlinedButton(
                onClick = { vm.startEntraDeviceCode(context) },
                enabled = uiState !is LoginUiState.Loading,
                modifier = Modifier.fillMaxWidth().height(52.dp),
                shape = RoundedCornerShape(12.dp),
                border = ButtonDefaults.outlinedButtonBorder.copy(
                    width = 1.5.dp
                ),
            ) {
                if (uiState is LoginUiState.Loading && (uiState as? LoginUiState.Loading)?.source == "entra") {
                    CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp)
                } else {
                    Text("Mit Microsoft anmelden", color = MaterialTheme.colorScheme.onBackground,
                        fontWeight = FontWeight.Medium)
                }
            }

            // Entra device code info
            val state = uiState
            if (state is LoginUiState.EntraDeviceCode) {
                Spacer(Modifier.height(20.dp))
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
                    shape = RoundedCornerShape(12.dp),
                ) {
                    Column(modifier = Modifier.padding(16.dp)) {
                        Text("Microsoft Login", fontWeight = FontWeight.Bold,
                            color = MaterialTheme.colorScheme.onSurface)
                        Spacer(Modifier.height(8.dp))
                        Text("Besuche:", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp)
                        Text(state.verificationUri, color = MSPColors.Cyan, fontWeight = FontWeight.Medium)
                        Spacer(Modifier.height(8.dp))
                        Text("Und gib diesen Code ein:", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp)
                        Text(state.userCode, fontSize = 28.sp, fontWeight = FontWeight.Bold,
                            color = MaterialTheme.colorScheme.onSurface, letterSpacing = 4.sp)
                        Spacer(Modifier.height(8.dp))
                        LinearProgressIndicator(modifier = Modifier.fillMaxWidth(), color = MSPColors.Cyan)
                        Spacer(Modifier.height(4.dp))
                        Text("Warte auf Microsoft-Bestätigung…",
                            color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 12.sp)
                    }
                }
            }

            // Error
            if (state is LoginUiState.Error) {
                Spacer(Modifier.height(16.dp))
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    colors = CardDefaults.cardColors(containerColor = MSPColors.Error.copy(alpha = 0.15f)),
                    shape = RoundedCornerShape(12.dp),
                ) {
                    Row(modifier = Modifier.padding(12.dp), verticalAlignment = Alignment.Top) {
                        Icon(Icons.Default.Warning, null, tint = MSPColors.Warning, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(8.dp))
                        Text(state.message, color = MaterialTheme.colorScheme.onSurface, fontSize = 13.sp)
                    }
                }
            }

            Spacer(Modifier.height(48.dp))
        }
    }
}
