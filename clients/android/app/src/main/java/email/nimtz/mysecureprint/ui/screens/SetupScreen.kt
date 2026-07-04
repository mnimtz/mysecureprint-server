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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import email.nimtz.mysecureprint.data.store.SettingsStore
import email.nimtz.mysecureprint.ui.MSPColors

@Composable
fun SetupScreen(settings: SettingsStore, onServerReady: () -> Unit) {
    var draftUrl by remember { mutableStateOf(settings.serverUrl) }
    var error by remember { mutableStateOf("") }

    fun isValid(url: String): Boolean {
        val s = url.trim()
        return (s.startsWith("http://") || s.startsWith("https://")) && s.length > 10
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(
                Brush.verticalGradient(
                    listOf(MSPColors.Navy, MSPColors.NavyLight)
                )
            )
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 28.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Spacer(Modifier.height(80.dp))

            // Logo / header
            Icon(
                imageVector = Icons.Default.Print,
                contentDescription = null,
                tint = MSPColors.Cyan,
                modifier = Modifier.size(56.dp)
            )
            Spacer(Modifier.height(16.dp))
            Text(
                text = "MySecurePrint",
                color = MaterialTheme.colorScheme.onBackground,
                fontSize = 28.sp,
                fontWeight = FontWeight.Bold,
            )
            Text(
                text = "Server einrichten",
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                fontSize = 15.sp,
            )

            Spacer(Modifier.height(48.dp))

            // URL field
            OutlinedTextField(
                value = draftUrl,
                onValueChange = { draftUrl = it; error = "" },
                label = { Text("Server-URL") },
                placeholder = { Text("https://msp.example.com") },
                leadingIcon = { Icon(Icons.Default.Cloud, null) },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                modifier = Modifier.fillMaxWidth(),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = MSPColors.Cyan,
                    focusedLabelColor = MSPColors.Cyan,
                ),
                isError = error.isNotEmpty(),
                supportingText = if (error.isNotEmpty()) {{ Text(error) }} else null,
            )

            Spacer(Modifier.height(8.dp))

            Text(
                text = "Den QR-Code für die schnelle Einrichtung findest du im Self-Service-Bereich des Management-Portals.",
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                fontSize = 12.sp,
                textAlign = TextAlign.Center,
                modifier = Modifier.padding(horizontal = 8.dp),
            )

            Spacer(Modifier.height(32.dp))

            Button(
                onClick = {
                    val url = draftUrl.trim().trimEnd('/')
                    val normalized = when {
                        url.startsWith("http://") || url.startsWith("https://") -> url
                        url.isNotBlank() -> "https://$url"
                        else -> ""
                    }
                    if (!isValid(normalized)) {
                        error = "Bitte eine gültige URL eingeben (https://...)."
                        return@Button
                    }
                    settings.serverUrl = normalized
                    onServerReady()
                },
                enabled = draftUrl.isNotBlank(),
                modifier = Modifier
                    .fillMaxWidth()
                    .height(52.dp),
                colors = ButtonDefaults.buttonColors(containerColor = MSPColors.Cyan),
                shape = RoundedCornerShape(12.dp),
            ) {
                Text("Weiter zum Login", fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
                Spacer(Modifier.width(8.dp))
                Icon(Icons.Default.ArrowForward, contentDescription = null)
            }

            Spacer(Modifier.height(48.dp))
        }
    }
}
