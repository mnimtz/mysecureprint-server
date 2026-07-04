package email.nimtz.mysecureprint.ui.screens

import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel
import email.nimtz.mysecureprint.data.store.SettingsStore
import email.nimtz.mysecureprint.ui.MSPColors
import email.nimtz.mysecureprint.ui.viewmodels.UploadUiState
import email.nimtz.mysecureprint.ui.viewmodels.UploadViewModel
import email.nimtz.mysecureprint.ui.viewmodels.UploadViewModelFactory

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun UploadScreen(settings: SettingsStore, initialSharedFileUri: Uri?) {
    val context = LocalContext.current
    val vm: UploadViewModel = viewModel(factory = UploadViewModelFactory(settings))
    val uiState by vm.uiState.collectAsState()

    var selectedUri by remember { mutableStateOf<Uri?>(initialSharedFileUri) }
    var selectedFilename by remember { mutableStateOf("") }
    val snackbarHostState = remember { SnackbarHostState() }

    // If a file was shared into the app, start upload automatically
    LaunchedEffect(initialSharedFileUri) {
        if (initialSharedFileUri != null) {
            selectedUri = initialSharedFileUri
        }
    }

    LaunchedEffect(uiState) {
        if (uiState is UploadUiState.Success) {
            snackbarHostState.showSnackbar("Datei erfolgreich hochgeladen!")
            vm.resetState()
            selectedUri = null
            selectedFilename = ""
        }
        if (uiState is UploadUiState.Error) {
            snackbarHostState.showSnackbar((uiState as UploadUiState.Error).message)
            vm.resetState()
        }
    }

    // Update filename when URI changes
    LaunchedEffect(selectedUri) {
        val uri = selectedUri ?: return@LaunchedEffect
        context.contentResolver.query(uri, null, null, null, null)?.use { cursor ->
            if (cursor.moveToFirst()) {
                val col = cursor.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME)
                if (col >= 0) selectedFilename = cursor.getString(col) ?: ""
            }
        }
    }

    val filePicker = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.OpenDocument()
    ) { uri: Uri? ->
        selectedUri = uri
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Upload") },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MSPColors.Navy, titleContentColor = Color.White),
            )
        },
        snackbarHost = { SnackbarHost(snackbarHostState) },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .padding(20.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Spacer(Modifier.height(8.dp))

            // File picker card
            Card(
                modifier = Modifier.fillMaxWidth(),
                onClick = {
                    filePicker.launch(
                        arrayOf("application/pdf", "image/*", "application/msword",
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            "application/vnd.ms-excel",
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                    )
                },
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
                shape = RoundedCornerShape(16.dp),
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(24.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Icon(
                        imageVector = if (selectedUri != null) Icons.Default.InsertDriveFile else Icons.Default.FileUpload,
                        contentDescription = null,
                        tint = if (selectedUri != null) MSPColors.Cyan else MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(48.dp),
                    )
                    Spacer(Modifier.height(12.dp))
                    if (selectedUri != null && selectedFilename.isNotBlank()) {
                        Text(
                            selectedFilename,
                            fontWeight = FontWeight.Medium,
                            color = MaterialTheme.colorScheme.onSurface,
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis,
                        )
                        Spacer(Modifier.height(4.dp))
                        Text("Tippen zum Ändern", fontSize = 12.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    } else {
                        Text("Datei auswählen", fontWeight = FontWeight.Medium,
                            color = MaterialTheme.colorScheme.onSurface)
                        Spacer(Modifier.height(4.dp))
                        Text("PDF, Bilder, Word, Excel", fontSize = 12.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }

            Spacer(Modifier.height(20.dp))

            // Queue info
            if (settings.defaultQueueName.isNotBlank()) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Icon(Icons.Default.Print, null, tint = MSPColors.Cyan, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(8.dp))
                    Text("Druckziel: ", fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(settings.defaultQueueName, fontSize = 13.sp,
                        color = MaterialTheme.colorScheme.onSurface, fontWeight = FontWeight.Medium,
                        maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                }
                Spacer(Modifier.height(16.dp))
            } else {
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    colors = CardDefaults.cardColors(containerColor = MSPColors.Warning.copy(alpha = 0.1f)),
                    shape = RoundedCornerShape(10.dp),
                ) {
                    Row(modifier = Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Default.Info, null, tint = MSPColors.Warning, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(8.dp))
                        Text("Kein Druckziel ausgewählt. Bitte erst im Tab „Ziele" ein Ziel wählen.",
                            fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurface)
                    }
                }
                Spacer(Modifier.height(16.dp))
            }

            // Upload button
            val isUploading = uiState is UploadUiState.Uploading
            Button(
                onClick = {
                    val uri = selectedUri ?: return@Button
                    vm.upload(context, uri)
                },
                enabled = selectedUri != null && !isUploading,
                modifier = Modifier.fillMaxWidth().height(52.dp),
                colors = ButtonDefaults.buttonColors(containerColor = MSPColors.Cyan),
                shape = RoundedCornerShape(12.dp),
            ) {
                if (isUploading) {
                    CircularProgressIndicator(modifier = Modifier.size(20.dp), color = MSPColors.Navy, strokeWidth = 2.dp)
                    Spacer(Modifier.width(12.dp))
                    Text("Hochladen…", fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
                } else {
                    Icon(Icons.Default.Send, null)
                    Spacer(Modifier.width(8.dp))
                    Text("Senden", fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
                }
            }
        }
    }
}
